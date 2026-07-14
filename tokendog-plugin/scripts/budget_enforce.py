#!/usr/bin/env python3
# tokendog-plugin/scripts/budget_enforce.py
"""PreToolUse hook: deny tool use when the hard budget is exceeded.
Fails open — any error results in NO deny (never blocks legitimate work)."""
import json
import os
import sys

def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
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
