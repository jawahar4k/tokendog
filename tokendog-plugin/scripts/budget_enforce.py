#!/usr/bin/env python3
# tokendog-plugin/scripts/budget_enforce.py
"""PreToolUse hook: deny tool use when the hard budget is exceeded.
Fails open — any error results in NO deny (never blocks legitimate work)."""
import json
import os
import sys

# Hooks run under whatever `python3` is first on PATH, which is often not the
# environment tokendog was installed into. _bootstrap re-execs us under one
# that works; without it the ImportError below is swallowed and the hook
# silently records nothing forever. Must run before stdin is read.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _bootstrap import ensure_tokendog
except Exception:  # pragma: no cover - bootstrap absent; behave as before
    def ensure_tokendog() -> bool:
        return True


def _no_tokendog() -> int:
    """No interpreter on this machine can import tokendog.

    Stay silent and fail open, as every hook must. `sink_health` is the one
    place that reports this out loud, once per session at SessionStart.
    """
    return 0


def main() -> int:
    if not ensure_tokendog():
        return _no_tokendog()
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    # Observe-only mode never blocks a call — it just watches spend.
    if str(os.environ.get("TOKENDOG_OBSERVE_ONLY")).strip().lower() in ("1", "true", "yes", "on"):
        return 0
    try:
        from tokendog.budget import check
    except Exception:
        return 0
    try:
        session_id = payload.get("session_id")
        glitch = os.path.join(os.getcwd(), ".glitch", "firmware", "firmware.db")
        result = check(session_id=session_id, glitch_db=glitch if os.path.exists(glitch) else None)
        if result["over_daily"] or result["over_session"]:
            reason = (f"TokenDog budget exceeded — today ${result['daily']:.2f}"
                      f", session ${result['session']:.2f}. Raise it with /tokendog:budget.")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }}))
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
