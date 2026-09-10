#!/usr/bin/env python3
"""Stop hook: emit a one-line token-spend summary for the session."""
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


def _quiet() -> bool:
    """Suppress the end-of-turn summary when the reader has asked for silence.

    The summary is a `systemMessage` — informational, never blocking — but it
    prints after every turn, and some readers would rather not see it. Opt out
    with TOKENDOG_QUIET=1 (or =true/yes/on). Everything else the Stop hooks do
    (token counting, budget alerts) is unaffected; this only mutes the line.
    """
    return str(os.environ.get("TOKENDOG_QUIET", "")).strip().lower() in (
        "1", "true", "yes", "on")


def main() -> int:
    if _quiet():
        return 0
    if not ensure_tokendog():
        return _no_tokendog()
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        from tokendog.budget import spend_session
        from tokendog.report import cost_summary
        from tokendog.sink import sink_health
    except Exception:
        return 0
    try:
        session_id = payload.get("session_id")
        if not session_id:
            return 0
        spend = spend_session(session_id)
        rows = cost_summary(group_by="session_id")["rows"]
        calls = sum(r["calls"] for r in rows if r["key"] == session_id)
        if spend <= 0 and calls == 0:
            return 0
        # Cost now comes from authoritative per-turn usage, so it is no longer
        # a local approximation. The only caveat worth surfacing is a sink that
        # has stopped recording.
        qualifier = " — telemetry sink DEGRADED, figures may be incomplete" if sink_health().get("degraded") else ""
        msg = (f"TokenDog: this session ~${spend:.4f} at API list prices across "
               f"{calls} recorded tool calls{qualifier} — attribution, not a bill "
               f"(a subscription has no per-token charge). "
               f"Run /tokendog:cost for the full breakdown.")
        print(json.dumps({"systemMessage": msg}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
