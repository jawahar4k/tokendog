#!/usr/bin/env python3
"""PreToolUse hook on `Read`: stop a large whole-file read before it lands.

SAFE BY DEFAULT. Modes (env TOKENDOG_READGUARD_MODE):
  off      — do nothing at all.
  shadow   — (DEFAULT) record what it WOULD have suggested; never intervenes.
  enforce  — ask the model to read a range, grep, or delegate, once per file.
TOKENDOG_OBSERVE_ONLY=1 forces shadow.

Even in enforce this is a speed bump, not a wall: a second Read of the same
file in the same session is always allowed. The suggestion is only made when
the arithmetic says it pays — see `tokendog.readguard` for the cost rule.
"""
import json
import os
import sys

# Hooks run under whatever `python3` is first on PATH, which is often not the
# environment tokendog was installed into. _bootstrap re-execs us under one
# that works; without it the ImportError below is swallowed and the hook
# silently does nothing forever. Must run before stdin is read.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _bootstrap import ensure_tokendog
except Exception:  # pragma: no cover - bootstrap absent; behave as before
    def ensure_tokendog() -> bool:
        return True


def _no_tokendog() -> int:
    """No interpreter here can import tokendog.

    Stay silent and fail open, as every hook must. `sink_health` is the one
    place that reports this out loud, once per session at SessionStart.
    """
    return 0


def _context_tokens(session_id) -> int | None:
    """What the window currently holds, from this session's cached split.

    The PreToolUse payload does not carry the context size, so the guard reads
    the figure the Stop hook last wrote. No split cached yet means no decision:
    the cost rule needs a context, and guessing one would make the guard fire
    in exactly the sessions it should not.
    """
    try:
        from tokendog.ledger import read_cached_split
        split = read_cached_split(session_id)
        return int(split["total"]) if split else None
    except Exception:
        return None


def main() -> int:
    if not ensure_tokendog():
        return _no_tokendog()
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Read":
        return 0
    try:
        from tokendog.readguard import assess, mode
        from tokendog.readguard_log import record, seen_paths
    except Exception:
        return 0
    try:
        current = mode()
        if current == "off":
            return 0
        session_id = payload.get("session_id")
        decision = assess(payload.get("tool_input") or {},
                          context_tokens=_context_tokens(session_id),
                          seen=seen_paths(session_id))
        record(session_id, decision, mode=current)
        # Shadow records and stops there — that is the whole point of shadow.
        if decision.action == "suggest" and current == "enforce":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.reason,
            }}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
