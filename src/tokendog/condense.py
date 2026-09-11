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
_READERS = frozenset(("cat", "bat", "nl", "head", "tail", "less", "more", "sed", "awk", "tac"))
_FAIL = re.compile(r"(?i)\b(error|fail(ed|ure)?|exception|traceback|assert|"
                   r"warning|panic|fatal|✗|✘|denied|not found|cannot|undefined)\b")


_SEGMENT = re.compile(r"&&|\|\||;|\||\n")
_PREAMBLE = frozenset(("do", "then", "else", "sudo", "time", "env", "exec", "command", "(", "{"))


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
    for segment in _SEGMENT.split(command or ""):
        words = segment.split()
        while words and (words[0] in _PREAMBLE
                         or ("=" in words[0] and not words[0].startswith("-"))):
            words.pop(0)                  # `do`, `sudo`, leading VAR=value …
        if not words:
            continue
        head = words[0].rsplit("/", 1)[-1]
        if head in _READERS:
            return True
        # A diff is read for its hunks; the middle is not filler either.
        if head == "diff" or (head == "git" and len(words) > 1 and words[1] in ("diff", "show")):
            return True
    return False


def _footer(total_lines: int, hint: str) -> str:
    return f"\n… [condensed by TokenDog — {total_lines} lines total; {hint}]"


def _head_tail(text: str, head: int, tail: int, hint: str) -> str:
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text
    kept = lines[:head] + [f"… [{len(lines) - head - tail} lines elided] …"] + lines[-tail:]
    return "\n".join(kept) + _footer(len(lines), hint)


def deterministic_condense(text: str, tool_name: str, command: str) -> tuple[str | None, str]:
    """(digest, method) with no model, or (None, "") if not confidently reducible.

    Only returns a digest it is sure preserves the useful part; anything
    ambiguous is left to the worker (or to plain truncation upstream).
    """
    lines = text.splitlines()
    n = len(lines)

    # A file the model asked to read is never cut, by any tier. This goes first
    # because `cat f; grep …` would otherwise hit the grep tier and keep the
    # first 80 lines — the file's head, not the matches.
    if _is_file_read(tool_name, command):
        return None, ""

    # grep/search output IS already the matches — keep the first block, count the rest.
    if tool_name == "Bash" and _GREP.search(command or ""):
        cap = 80
        if n <= cap:
            return None, ""
        kept = lines[:cap]
        return ("\n".join(kept) + _footer(n, f"first {cap} matches shown, {n - cap} more — "
                "narrow the pattern or add a path to see specific ones"), "grep-matches")

    # test/build output — keep the failure lines plus head and tail (the summary).
    if tool_name == "Bash" and _TESTY.search(command or ""):
        fails = [ln for ln in lines if _FAIL.search(ln)]
        if fails and len(fails) < n * 0.5:      # a mostly-passing run with some failures
            head, tail = lines[:8], lines[-8:]
            body = fails[:120]
            digest = ("\n".join(head)
                      + f"\n… [showing {len(body)} of {len(fails)} error/fail lines] …\n"
                      + "\n".join(body) + "\n… [tail] …\n" + "\n".join(tail)
                      + _footer(n, "re-run the command for the full log"))
            if approx_tokens(digest) < approx_tokens(text):
                return digest, "errors"

    # generic large COMMAND output (a build, a log, a listing) — head + tail with
    # the middle elided. A file read never reaches here: see the top of this function.
    if n > 220:
        return _head_tail(text, head=120, tail=60,
                          hint="re-run with a range/filter, or Read specific lines, for the middle"), "head-tail"
    return None, ""


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
    result = {"raw_tokens": raw_tok, "digest": text, "digest_tokens": raw_tok,
              "method": "none", "reduced": False, "worker_used": False}

    digest, method = deterministic_condense(text, tool_name, command)
    if digest is not None and approx_tokens(digest) < raw_tok:
        result.update(digest=digest, digest_tokens=approx_tokens(digest),
                      method=method, reduced=True)
        return result

    # Deterministic couldn't confidently reduce it. Try the worker if permitted.
    if use_worker and worker_available():
        w = haiku_condense(text, target_tokens=max_tokens, tool_name=tool_name, command=command)
        if w and approx_tokens(w) < raw_tok:
            w = w + _footer(len(text.splitlines()), "worker summary — re-run for the raw output")
            result.update(digest=w, digest_tokens=approx_tokens(w),
                          method="haiku", reduced=True, worker_used=True)
            return result

    return result
