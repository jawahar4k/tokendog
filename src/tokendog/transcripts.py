from __future__ import annotations
import json
import os
from pathlib import Path, PurePosixPath
from typing import Iterator
from .event import TokenEvent, RUNTIME_CLAUDE, SOURCE_TRANSCRIPT

# Claude Code writes a JSONL transcript per session under ~/.claude/projects/.
# Every assistant record carries a `message.usage` block with AUTHORITATIVE
# token counts — the same numbers the API billed. Reading them replaces the
# tiktoken approximation entirely.
#
# DEFINITION OF A TURN (fix this here so consumers don't each pick their own):
#   one metered turn == one assistant record whose `message.usage` is present.
# Records without usage (user turns, tool results, meta records) are not turns
# and are skipped. A record's `message.usage` is the usage for that record
# alone; do not additionally sum any nested per-iteration usage, or every turn
# is counted twice.

DEFAULT_TRANSCRIPT_ROOT = "~/.claude/projects"


def transcript_root() -> Path:
    return Path(os.environ.get("TOKENDOG_TRANSCRIPT_ROOT",
                               DEFAULT_TRANSCRIPT_ROOT)).expanduser()


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def split_cache_creation(usage: dict) -> tuple[int, int, int]:
    """Return (total, ephemeral_5m, ephemeral_1h) cache-creation tokens.

    The nested `cache_creation` object is the richer signal and is preferred.
    When only the `cache_creation_input_tokens` scalar is present the TTL is
    genuinely unknown; the remainder is attributed to the 5-minute bucket,
    which is the cheaper multiplier — so an unsplit source under-states rather
    than over-states cost, and never silently inflates it.
    """
    total = _int(usage.get("cache_creation_input_tokens"))
    nested = usage.get("cache_creation")
    five_m = one_h = 0
    if isinstance(nested, dict):
        five_m = _int(nested.get("ephemeral_5m_input_tokens"))
        one_h = _int(nested.get("ephemeral_1h_input_tokens"))
    if not total:
        total = five_m + one_h
    remainder = total - (five_m + one_h)
    if remainder > 0:
        five_m += remainder
    return total, five_m, one_h


def project_of(record: dict) -> str | None:
    """Project name for a transcript record: the basename of its `cwd`.

    Claude Code records `cwd` on every turn, so attribution is exact rather
    than inferred. The containing directory name encodes the same path with
    `/` replaced by `-`, which cannot be decoded unambiguously — a project
    called `my-app` is indistinguishable from a directory `my/app` — so when
    `cwd` is absent this returns None rather than guess wrong.

    Basename only, for the same reason `ingest.redact_path` exists: the full
    path carries the home directory and frequently a client or product name.
    """
    cwd = record.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    return PurePosixPath(cwd.strip().rstrip("/")).name or None


def event_from_record(record: dict, *, session_id: str | None = None,
                      transcript_id: str | None = None) -> TokenEvent | None:
    """Build a TokenEvent from one transcript record, or None if it is not a turn."""
    if not isinstance(record, dict):
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None

    total_cc, five_m, one_h = split_cache_creation(usage)
    return TokenEvent(
        ts=record.get("timestamp") or "",
        session_id=record.get("sessionId") or session_id or "unknown",
        transcript_id=transcript_id or session_id,
        runtime=RUNTIME_CLAUDE,
        event="assistant-turn",
        source=SOURCE_TRANSCRIPT,
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
        cache_creation_tokens=total_cc,
        cache_creation_5m_tokens=five_m,
        cache_creation_1h_tokens=one_h,
        service_tier=usage.get("service_tier"),
        inference_geo=usage.get("inference_geo"),
        model=message.get("model"),
        project=project_of(record),
    )


def read_transcript(path) -> Iterator[TokenEvent]:
    """Yield one TokenEvent per metered turn in a single transcript file."""
    p = Path(path)
    session_id = p.stem
    try:
        # errors="replace", not strict: a single undecodable byte partway
        # through a transcript would otherwise raise mid-iteration and
        # silently drop every remaining turn in that file — i.e. undercount
        # the bill without saying so.
        handle = p.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            event = event_from_record(record, session_id=session_id,
                                      transcript_id=session_id)
            if event is not None:
                yield event


def read_transcripts(root=None) -> Iterator[TokenEvent]:
    base = Path(root).expanduser() if root is not None else transcript_root()
    if not base.exists():
        return
    for jf in sorted(base.rglob("*.jsonl")):
        yield from read_transcript(jf)


def known_projects(root=None, max_lines: int = 40) -> list[str]:
    """Project names present in the transcript tree, cheaply.

    Reads only the first few lines of each file rather than every turn: this
    exists so `doctor` can tell you what `--project` accepts without paying a
    full ingest. Returns basenames, so it never prints a home directory back at
    you — the containing directory names are full paths with `/` turned into
    `-`, which is both unusable as a filter value and a privacy leak.
    """
    base = Path(root).expanduser() if root is not None else transcript_root()
    if not base.exists():
        return []
    found: set[str] = set()
    for jf in sorted(base.rglob("*.jsonl")):
        try:
            with jf.open(encoding="utf-8", errors="replace") as f:
                for _, line in zip(range(max_lines), f):
                    if '"cwd"' not in line:
                        continue
                    try:
                        name = project_of(json.loads(line))
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if name:
                        found.add(name)
                        break
        except OSError:
            continue
    return sorted(found)
