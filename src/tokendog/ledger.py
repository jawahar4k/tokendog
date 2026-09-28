from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Split the window by WHAT THE READER CAN DO ABOUT IT.
#
# Everything in the context window is re-sent on every turn, so the only useful
# question about a token is which lever moves it:
#
#   setup  re-sent definitions. Shrunk by DISABLING something — a connector, a
#          skill, an agent. `/clear` does not touch it; it is re-injected.
#   work   the history. Shrunk by `/clear` or `/compact`, and by nothing else.
#
# A percentage cannot tell those apart, which is why "83% full" leads people to
# compact a session whose weight is a connector they never call.
#
# THE ONE RULE: totals are exact, only the split is estimated. Each turn's total
# comes from the API's own `usage`; the growth since the previous turn is shared
# across whatever arrived in between, in proportion. Nothing here ever invents a
# total, and nothing here is ever priced — `pricing` does that, from usage.

SETUP_KINDS = ("sys", "mcp", "skills", "agents")
WORK_KINDS = ("tools", "mcp_results", "skill_results", "chat", "other")

# Tokens per naive chars/4 unit. Real transcript text runs nearer 2.1 chars per
# token than 4, so chars/4 understates by roughly this much. A session measures
# its own value once it has seen enough; this is the starting point.
DEFAULT_RATIO = 1.9
RATIO_MIN, RATIO_MAX = 1.0, 4.0
RATIO_MIN_TURN_CHARS = 1_000      # a turn too small to be evidence of anything
RATIO_MIN_OBSERVED = 20_000       # measure only once this much has been seen

# Attachment kinds that carry setup, and the bucket each lands in. Keys are the
# attachment `type`; values are (bucket, field, shape).
_SETUP_ATTACHMENTS = {
    "prompt_snapshot":        ("sys", "systemPrompt", "list"),
    "instructions":           ("sys", "files", "files"),
    "skill_listing":          ("skills", "content", "str"),
    "agent_listing_delta":    ("agents", "addedLines", "list"),
    "deferred_tools_delta":   ("mcp", "addedLines", "list"),
    "deferred_tools_record":  ("mcp", "entries", "entries"),
    "mcp_instructions_delta": ("mcp", "addedBlocks", "list"),
}
# Attachment kinds that are work: content pulled in for this task.
_WORK_ATTACHMENTS = {"file": "tools", "edited_text_file": "tools", "directory": "tools"}


@dataclass
class Split:
    """One session's window, divided by lever. Values are tokens."""

    setup: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SETUP_KINDS, 0))
    work: dict[str, int] = field(default_factory=lambda: dict.fromkeys(WORK_KINDS, 0))
    total: int = 0
    ratio: float = DEFAULT_RATIO
    turns: int = 0
    segments: int = 0
    observed_chars: int = 0

    @property
    def setup_total(self) -> int:
        return sum(self.setup.values())

    @property
    def work_total(self) -> int:
        return sum(self.work.values())

    def as_dict(self) -> dict:
        return {"setup": dict(self.setup), "work": dict(self.work),
                "setup_total": self.setup_total, "work_total": self.work_total,
                "total": self.total, "ratio": round(self.ratio, 3),
                "turns": self.turns, "segments": self.segments}


def _chars(value) -> int:
    """Characters in a string, a list of strings, or a list of records."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        n = 0
        for item in value:
            if isinstance(item, str):
                n += len(item)
            elif isinstance(item, dict):
                n += sum(len(v) for v in item.values() if isinstance(v, str))
        return n
    return 0


def _result_bucket(tool_name: str) -> str:
    if tool_name.startswith("mcp__"):
        return "mcp_results"
    if tool_name in ("Skill", "SlashCommand"):
        return "skill_results"
    return "tools"


def _usage_total(usage: dict) -> int:
    """What the window held for this turn, from the API's own numbers."""
    return int((usage.get("input_tokens") or 0)
               + (usage.get("cache_creation_input_tokens") or 0)
               + (usage.get("cache_read_input_tokens") or 0))


class _Accumulator:
    """Pending characters per category, settled at each assistant turn."""

    def __init__(self) -> None:
        self.pend: dict[str, int] = {}
        self.tool_names: dict[str, str] = {}

    def add(self, bucket: str, chars: int) -> None:
        if chars > 0:
            self.pend[bucket] = self.pend.get(bucket, 0) + chars

    def take(self) -> dict[str, int]:
        out, self.pend = self.pend, {}
        return out


def _read(path) -> list[dict]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def session_split(path, *, records=None) -> Split:
    """Divide one session's window into setup and work.

    Reads a transcript (or a list of records) and returns tokens per category,
    anchored to the exact per-turn totals. Never raises: an unreadable file or a
    malformed record yields a smaller answer, not an exception, because this
    feeds a statusline that must print something on every keystroke.
    """
    recs = records if records is not None else _read(path)
    acc = _Accumulator()
    split = Split()

    seen_ids: set[str] = set()
    last_total = 0
    pinned_sys = 0
    first_turn_of_session = True
    segment_first_turn = True
    segments = 0
    # Ratio evidence: tokens the API charged for growth, against the chars we
    # actually watched arrive. Only turns big enough to mean something count.
    ratio_tokens = ratio_chars = 0

    for rec in recs:
        kind = rec.get("type")

        if kind == "system" and rec.get("subtype") == "compact_boundary":
            # The window is rebuilt: the history is gone and setup is re-injected.
            # So the split describes the CURRENT window — everything attributed
            # so far is discarded — except `sys`, which is re-sent unchanged and
            # was measured once, on the only turn where it could be.
            acc.take()
            split.setup = dict.fromkeys(SETUP_KINDS, 0)
            split.work = dict.fromkeys(WORK_KINDS, 0)
            split.setup["sys"] = pinned_sys
            last_total = 0
            segment_first_turn = True
            continue

        if rec.get("isSidechain"):
            continue

        if kind == "attachment":
            a = rec.get("attachment")
            if not isinstance(a, dict):
                continue
            at = a.get("type")
            if at in _SETUP_ATTACHMENTS:
                bucket, fieldname, _shape = _SETUP_ATTACHMENTS[at]
                acc.add(bucket, _chars(a.get(fieldname)))
            elif at in _WORK_ATTACHMENTS:
                acc.add(_WORK_ATTACHMENTS[at], _chars(a.get("content") or a.get("snippet")))
            continue

        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")

        if kind == "user":
            for b in (content or []):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    name = acc.tool_names.get(b.get("tool_use_id"), "")
                    acc.add(_result_bucket(name), _chars(b.get("content")))
                elif b.get("type") == "text":
                    acc.add("chat", _chars(b.get("text")))
            if isinstance(content, str):
                acc.add("chat", len(content))
            continue

        if kind != "assistant":
            continue

        for b in (content or []):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                acc.tool_names[b.get("id")] = b.get("name") or ""
            elif b.get("type") == "text":
                acc.add("chat", _chars(b.get("text")))

        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        total = _usage_total(usage)
        if total <= 0:
            continue                       # synthetic turn (a rate-limit notice)
        mid = msg.get("id") or rec.get("uuid")
        if mid in seen_ids:
            continue                       # several records, one API message
        seen_ids.add(mid)

        pend = acc.take()
        observed_chars = sum(pend.values())
        split.observed_chars += observed_chars
        split.turns += 1
        if segment_first_turn:
            segments += 1

        ratio = (max(RATIO_MIN, min(RATIO_MAX, ratio_tokens / (ratio_chars / 4)))
                 if ratio_chars >= RATIO_MIN_OBSERVED and ratio_chars else DEFAULT_RATIO)
        growth = total - last_total

        if segment_first_turn:
            # The system prompt and the built-in tool definitions are never
            # written to the transcript, so on a first turn whatever the exact
            # total does not account for IS them. After a compaction that same
            # unexplained bulk is NOT a second system prompt — it is the MCP
            # schemas ToolSearch loaded on demand earlier, re-sent in the
            # rebuilt prefix. Pinning sys to the session's first segment keeps
            # the number the reader uses to decide what to disable honest.
            named = _distribute(pend, ratio, cap=total)
            remainder = max(0, total - sum(named.values()) - split.setup["sys"])
            _merge(split, named)
            if first_turn_of_session:
                _bump(split, "sys", remainder)
                pinned_sys = split.setup["sys"]
                first_turn_of_session = False
            else:
                _bump(split, "mcp", remainder)
            segment_first_turn = False
        elif growth > 0:
            if observed_chars:
                _merge(split, _distribute(pend, ratio, cap=growth, exact=True))
                if observed_chars >= RATIO_MIN_TURN_CHARS:
                    ratio_tokens += growth
                    ratio_chars += observed_chars
            else:
                # Billed, but nothing visible arrived: extended thinking.
                _bump(split, "other", growth)
        elif growth < 0:
            _shrink_work(split, -growth)

        last_total = total

    split.total = last_total
    split.segments = segments
    split.ratio = (max(RATIO_MIN, min(RATIO_MAX, ratio_tokens / (ratio_chars / 4)))
                   if ratio_chars >= RATIO_MIN_OBSERVED and ratio_chars else DEFAULT_RATIO)
    _reconcile(split)
    return split


def _distribute(pend: dict[str, int], ratio: float, *, cap: int,
                exact: bool = False) -> dict[str, int]:
    """Turn pending characters into tokens, never exceeding what was billed.

    `exact` shares the whole of `cap` in proportion — the right thing for a
    turn's growth, which IS the observed content. Without it the estimate is
    kept as an estimate and only clipped, which is what a first turn needs: its
    unexplained remainder is a real quantity, not rounding.
    """
    if not pend:
        return {}
    chars = sum(pend.values())
    if exact:
        out, spent = {}, 0
        items = sorted(pend.items(), key=lambda kv: -kv[1])
        for i, (bucket, c) in enumerate(items):
            # The last bucket takes the remainder so the parts sum to `cap`
            # exactly: a split that loses a token to rounding stops adding up.
            give = cap - spent if i == len(items) - 1 else int(cap * c / chars)
            out[bucket] = give
            spent += give
        return out
    est = {b: int(c / 4 * ratio) for b, c in pend.items()}
    over = sum(est.values())
    if over > cap and over:
        est = {b: int(v * cap / over) for b, v in est.items()}
    return est


def _bump(split: Split, bucket: str, tokens: int) -> None:
    if tokens <= 0:
        return
    if bucket in split.setup:
        split.setup[bucket] += tokens
    else:
        split.work[bucket] = split.work.get(bucket, 0) + tokens


def _merge(split: Split, parts: dict[str, int]) -> None:
    for bucket, tokens in parts.items():
        _bump(split, bucket, tokens)


def _shrink_work(split: Split, tokens: int) -> None:
    """A window that got smaller lost history, never setup.

    Setup is re-sent verbatim every turn, so it cannot be what went away; a
    tool result that was cleared can be. Taken proportionally across work.
    """
    pool = sum(split.work.values())
    if pool <= 0:
        return
    take = min(tokens, pool)
    for bucket in list(split.work):
        split.work[bucket] -= int(take * split.work[bucket] / pool)
    drift = sum(split.work.values()) - (pool - take)
    if drift:
        largest = max(split.work, key=lambda b: split.work[b])
        split.work[largest] = max(0, split.work[largest] - drift)


def _reconcile(split: Split) -> None:
    """Make the parts sum to the exact total, and say where the gap went.

    Rounding, a clipped estimate, or a shrink that outran what we had attributed
    all leave the parts a little off the one number that is not an estimate. The
    difference belongs in `other`, which is already the honest bucket, rather
    than being spread around to hide it.
    """
    if split.total <= 0:
        for d in (split.setup, split.work):
            for k in d:
                d[k] = 0
        return
    gap = split.total - (split.setup_total + split.work_total)
    if gap > 0:
        split.work["other"] += gap
    elif gap < 0:
        _shrink_work(split, -gap)
        gap = split.total - (split.setup_total + split.work_total)
        if gap < 0:                          # work was already empty: clip setup
            largest = max(split.setup, key=lambda b: split.setup[b])
            split.setup[largest] = max(0, split.setup[largest] + gap)
