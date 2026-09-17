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


def _reason(result: dict) -> str:
    """Name the cap that actually tripped, and say whether waiting will help.

    The daily figure resets at midnight. The session figure does not reset at
    all: it is the session's whole life, and resuming keeps the same id — so a
    session cap, once crossed, stays crossed until it is raised. Saying only
    "budget exceeded" left people waiting for a reset that was never coming.
    """
    b = result["budget"]
    parts = []
    if result["over_daily"]:
        parts.append(f"today ${result['daily']:.2f} of ${b.daily_usd:.0f} (resets at midnight)")
    if result["over_session"]:
        parts.append(f"this session ${result['session']:.2f} of ${b.session_usd:.0f} — that is the "
                     f"whole session's spend, not today's, and it does not reset")
    return ("TokenDog budget exceeded: " + "; ".join(parts)
            + ". Figures are API list-price attribution across this machine, not a charge. "
            + "Raise it with /tokendog:budget --set-session N (or --set-daily N), or set "
            + "TOKENDOG_OBSERVE_ONLY=1 in the env block of settings.json to stop enforcing.")


def _is_budget_command(payload: dict) -> bool:
    """True for the Bash call a `/tokendog:budget` slash command makes."""
    if payload.get("tool_name") != "Bash":
        return False
    ti = payload.get("tool_input")
    cmd = ti.get("command", "") if isinstance(ti, dict) else ""
    return "tokendog_cli.py budget" in cmd or "tokendog.report budget" in cmd


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
    # The call that raises the cap must never be refused by the cap: once
    # crossed, `/tokendog:budget` is itself a Bash tool call, and denying it
    # left the only way out as hand-editing a JSON file in a terminal.
    if _is_budget_command(payload):
        return 0
    try:
        session_id = payload.get("session_id")
        glitch = os.path.join(os.getcwd(), ".glitch", "firmware", "firmware.db")
        result = check(session_id=session_id, glitch_db=glitch if os.path.exists(glitch) else None)
        if result["over_daily"] or result["over_session"]:
            reason = _reason(result)
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
