from __future__ import annotations

import json
import os
import re
import urllib.request

from .approx import approx_tokens

# Condense an oversized tool RESULT before it enters the model's context.
#
# The point is not a cheaper model — it is that a big blob is processed ONCE,
# here, and only a small digest enters the expensive model's re-read loop (where
# every carried token is billed again on every later turn). Two tiers:
#
#   deterministic   Free, no model, guaranteed smaller. Handles the dominant
#                   case — grep/log/test/command output — by KEEPING the lines
#                   that matter (matches, errors, head+tail) and dropping the
#                   rest. This is where most of the saving is.
#   worker (Haiku)  For output a machine cannot safely reduce by pattern — prose,
#                   a document, a mixed dump — ask a cheap model to summarise to
#                   a token budget. Opt-in (needs a key), guarded, timed out.
#
# TWO HARD RULES, because a condenser that makes things worse is worse than none:
#   1. The digest is CAPPED and must be smaller than the input; if a tier cannot
#      beat the input it is discarded and the next tier (or the original) is used.
#   2. Every digest ends with a pointer to the full output, so nothing is lost —
#      the model can re-run the command or read the range when it needs detail.

WORKER_MODEL = os.environ.get("TOKENDOG_WORKER_MODEL", "claude-haiku-4-5")
WORKER_TIMEOUT = float(os.environ.get("TOKENDOG_WORKER_TIMEOUT", "15"))
WORKER_MAX_INPUT_CHARS = 60_000     # never ship more than this to the worker

_GREP = re.compile(r"\b(grep|rg|ag|ack|egrep|fgrep)\b", re.I)
_TESTY = re.compile(r"\b(pytest|npm|pnpm|yarn|jest|vitest|cargo|go\s+test|tsc|"
                    r"eslint|ruff|mypy|make)\b", re.I)
# Commands whose output IS a file. Head+tail of a log tells the story; head+tail
# of a source file the model opened on purpose drops the part it opened it for.
# Replay over real transcripts put 91% of head-tail's projected saving here.
_READERS = frozenset(("cat", "bat", "nl", "less", "more", "sed", "awk", "tac"))
# head/tail are readers only as the command itself (`head -300 f`). Piped —
# `pytest | tail -20` — they are the model capping the output, the opposite case.
_LEADING_READERS = _READERS | {"head", "tail"}
_FAIL = re.compile(r"(?i)\b(error|fail(ed|ure)?|exception|traceback|assert|"
                   r"warning|panic|fatal|✗|✘|denied|not found|cannot|undefined)\b")


_SEGMENT = re.compile(r"&&|\|\||;|\||\n")
_PREAMBLE = frozenset(("do", "then", "else", "sudo", "time", "env", "exec", "command", "(", "{"))


# --- selection: what survives a cut -------------------------------------------
#
# One rule for all command output, rather than a tier per kind of command. The
# head and tail carry a run's story (what started, how it ended); problem lines
# carry the reason it ended that way, and the three lines after an error are
# usually the ones that say why. Everything else is dropped — and saved, and
# named by line range, so dropped is never lost.

def _env_int(name: str, default: int) -> int:
    """Unset, non-numeric or negative means default; 0 stays a valid value."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return default
    return default if value < 0 else value


# Every tunable has an override. These are ORBIT's measured values for its
# fleet; on a workload whose p90 command output is 5k tokens, an 8k floor means
# the condenser never runs, and the reader should be able to find that out.
MIN_CONDENSE_TOKENS = _env_int("TOKENDOG_CONDENSE_MIN_TOKENS", 8_000)
HEAD_LINES = _env_int("TOKENDOG_CONDENSE_HEAD", 30)
TAIL_LINES = _env_int("TOKENDOG_CONDENSE_TAIL", 60)
AFTER_LINES = _env_int("TOKENDOG_CONDENSE_AFTER", 3)       # after an error, the why
MAX_FRAMES = _env_int("TOKENDOG_CONDENSE_FRAMES", 20)      # past this it is the same trace
SIGNAL_BUDGET_TOKENS = _env_int("TOKENDOG_CONDENSE_BUDGET", 5_000)
CLIP_CHARS = _env_int("TOKENDOG_CONDENSE_CLIP", 2_000)     # one minified line is not readable
_CHARS_PER_TOKEN = 2.1         # measured on real transcripts, not the naive 4

_PROBLEM = re.compile(
    r"(?i)(\b(error|errors|fail|failed|failure|warn|warning|exception|traceback|fatal|panic|"
    r"denied|timeout|cannot|undefined)\b|timed?\s+out|not found|✗|✘)")
_FRAME = re.compile(r'^\s+(File ".*", line \d+|at [\w.$<>]+[ (]|at .+:\d+(:\d+)?\)?$|#\d+ )')
# Numbers and hashes are what make otherwise-identical log lines differ.
_NOISE = re.compile(r"\b[0-9a-f]{7,}\b|\d+")


def _is_file_read(tool_name: str, command: str) -> bool:
    """A whole-file read: the Read tool, or any stage of a Bash command that is a reader.

    Every segment is checked, not just the first: `cd x && cat f` and
    `for f in …; do cat $f; done` were the shapes replay found. A pipeline like
    `cat f | sed …` is still the file, transformed; erring toward "file" means
    erring toward not cutting.
    """
    if tool_name == "Read":
        return True
    if tool_name != "Bash":
        return False
    first = True
    for segment in _SEGMENT.split(command or ""):
        words = segment.split()
        while words and (words[0] in _PREAMBLE
                         or ("=" in words[0] and not words[0].startswith("-"))):
            words.pop(0)                  # `do`, `sudo`, leading VAR=value …
        if not words:
            continue
        head = words[0].rsplit("/", 1)[-1]
        if head == "cd":
            continue                      # `cd x && cat f`: the cat is still first
        if head in (_LEADING_READERS if first else _READERS):
            return True
        first = False
        # Where exact text matters a digest is wrong however good it is: a diff
        # is read for its hunks, a blame for its lines, jq for its values.
        if head in ("diff", "jq"):
            return True
        if head == "git" and len(words) > 1:
            if words[1] in ("diff", "show", "blame"):
                return True
            if words[1] == "log" and any(w in ("-p", "--patch", "-u") for w in words[2:]):
                return True
        if head == "gh" and words[1:3] == ["pr", "diff"]:
            return True
    return False


def _footer(total_lines: int, hint: str) -> str:
    return f"\n… [condensed by TokenDog — {total_lines} lines total; {hint}]"


def _line_tokens(line: str) -> int:
    return max(1, int(len(line) / _CHARS_PER_TOKEN))


def select_lines(lines: list[str]) -> list[int]:
    """0-based indices of the lines worth keeping, in order.

    Head and tail verbatim; then every problem line with the few lines after it
    and any stack frames that follow, until the signal budget runs out. The
    budget is what stops a log in which every line says "error" from turning
    the digest back into the input.
    """
    n = len(lines)
    keep = set(range(min(HEAD_LINES, n))) | set(range(max(0, n - TAIL_LINES), n))
    lo, hi = min(HEAD_LINES, n), max(0, n - TAIL_LINES)
    budget = SIGNAL_BUDGET_TOKENS
    i = lo
    while i < hi:
        if not _PROBLEM.search(lines[i]):
            i += 1
            continue
        window = {i}
        window.update(range(i + 1, min(hi, i + 1 + AFTER_LINES)))
        j, frames = i + 1, 0
        while j < hi and frames < MAX_FRAMES and _FRAME.match(lines[j]):
            window.add(j)
            j += 1
            frames += 1
        fresh = window - keep
        cost = sum(_line_tokens(lines[k]) for k in fresh)
        if cost > budget:
            break
        budget -= cost
        keep |= window
        i = max(window) + 1
    return sorted(keep)


def _clip(line: str) -> str:
    if len(line) <= CLIP_CHARS:
        return line
    return line[:CLIP_CHARS] + f" … [+{len(line) - CLIP_CHARS:,} chars]"


def _render(lines: list[str], kept: list[int]) -> str:
    """The digest: kept lines in order, gaps marked, middle repeats collapsed.

    Head and tail stay verbatim — they are where a reader looks first. Between
    them, a run of lines that differ only in numbers or hashes collapses to one
    line and a count, because `batch 1204 in 331ms` repeated four hundred times
    is one fact.
    """
    n = len(lines)
    head_end, tail_start = min(HEAD_LINES, n), max(0, n - TAIL_LINES)
    out: list[str] = []
    prev = -1
    run_key, run_line, run_count = None, None, 0

    def flush():
        nonlocal run_key, run_line, run_count
        if run_line is not None:
            out.append(run_line + (f"  ×{run_count}" if run_count > 1 else ""))
        run_key, run_line, run_count = None, None, 0

    for idx in kept:
        if prev >= 0 and idx > prev + 1:
            flush()
            out.append(f"… [{idx - prev - 1:,} lines not shown] …")
        line = _clip(lines[idx])
        if head_end <= idx < tail_start:
            key = _NOISE.sub("#", lines[idx])
            if key == run_key:
                run_count += 1
            else:
                flush()
                run_key, run_line, run_count = key, line, 1
        else:
            flush()
            out.append(line)
        prev = idx
    flush()
    return "\n".join(out)


def _ranges(kept: list[int]) -> list[tuple[int, int]]:
    """0-based indices → 1-based inclusive ranges, for the spill pointer."""
    out: list[tuple[int, int]] = []
    for idx in kept:
        if out and idx + 1 == out[-1][1] + 1:
            out[-1] = (out[-1][0], idx + 1)
        else:
            out.append((idx + 1, idx + 1))
    return out


def deterministic_condense(text: str, tool_name: str, command: str):
    """(digest, method, kept_ranges) with no model, or (None, "", []) when the
    output should be left alone.

    `kept_ranges` are the 1-based inclusive line ranges the digest contains.
    They make the cut reversible: the caller spills the full output first and
    names the gaps, so nothing is dropped without a pointer back to it.
    """
    # Where exact text matters — a file the model asked for, a diff, a blame —
    # a digest is wrong however good it is. Checked first, before any size test.
    if _is_file_read(tool_name, command):
        return None, "", []
    if approx_tokens(text) < MIN_CONDENSE_TOKENS:
        return None, "", []
    lines = text.splitlines()
    if len(lines) <= HEAD_LINES + TAIL_LINES:
        return None, "", []      # one enormous line: clipping it is not condensing it
    kept = select_lines(lines)
    if len(kept) >= len(lines):
        return None, "", []
    digest = _render(lines, kept) + _footer(len(lines), "the full output is saved; see below")
    return digest, "select", _ranges(kept)


def worker_available() -> bool:
    return bool(os.environ.get("TOKENDOG_WORKER_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def haiku_condense(text: str, target_tokens: int, tool_name: str, command: str) -> str | None:
    """Ask a cheap model to condense to a token budget. None on any failure.

    Anthropic Messages API over urllib (no SDK). Opt-in via a key. The INPUT is
    capped so a runaway result cannot itself become an expensive worker call.
    """
    key = os.environ.get("TOKENDOG_WORKER_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    clipped = text[:WORKER_MAX_INPUT_CHARS]
    prompt = (
        f"You are condensing the output of a `{tool_name}` command"
        + (f" (`{command[:120]}`)" if command else "")
        + f" so it can be handed to another engineer with a strict budget of about "
        f"{target_tokens} tokens. Keep everything that matters — errors, key values, "
        f"structure, conclusions — and drop noise, repetition and boilerplate. "
        f"Output ONLY the condensed text, no preamble.\n\n---\n{clipped}"
    )
    body = json.dumps({
        "model": WORKER_MODEL,
        "max_tokens": max(256, int(target_tokens * 1.3)),
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=WORKER_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    try:
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        digest = "".join(parts).strip()
    except Exception:
        return None
    return digest or None


def condense(text: str, *, tool_name: str = "", command: str = "",
             max_tokens: int = 1200, use_worker: bool = False) -> dict:
    """Condense `text`, deterministic-first, worker only if allowed and needed.

    Returns a report — never raises, never returns something LARGER than the
    input (falls back to the original if no tier beats it).
    """
    raw_tok = approx_tokens(text)
    total_lines = len(text.splitlines())
    result = {"raw_tokens": raw_tok, "digest": text, "digest_tokens": raw_tok,
              "method": "none", "reduced": False, "worker_used": False,
              "lines": total_lines,
              "kept_ranges": [(1, total_lines)] if total_lines else []}

    digest, method, kept = deterministic_condense(text, tool_name, command)
    # Grep is measured and never changed: whether a digest of search results
    # loses the match someone needed is a question for fleet data, not for a
    # rule. So the report can still say what it WOULD have saved.
    if tool_name == "Grep":
        result["would_reduce"] = digest is not None and approx_tokens(digest) < raw_tok
        return result
    if digest is not None and approx_tokens(digest) < raw_tok:
        result.update(digest=digest, digest_tokens=approx_tokens(digest),
                      method=method, reduced=True, kept_ranges=kept)
        return result

    # Deterministic couldn't confidently reduce it. Try the worker if permitted.
    if use_worker and worker_available():
        w = haiku_condense(text, target_tokens=max_tokens, tool_name=tool_name, command=command)
        if w and approx_tokens(w) < raw_tok:
            w = w + _footer(len(text.splitlines()), "worker summary — re-run for the raw output")
            # A worker summary is prose: no line of the original survives, so
            # every line is a gap and the spill pointer says so.
            result.update(digest=w, digest_tokens=approx_tokens(w),
                          method="haiku", reduced=True, worker_used=True,
                          kept_ranges=[])
            return result

    return result
