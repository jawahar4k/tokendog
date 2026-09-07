#!/usr/bin/env python3
"""SessionStart hook: warn once, visibly, if the telemetry sink has stopped working.

Every other hook swallows its exceptions so it can never crash a session. That
is the right trade, but it means a sink that has stopped accepting writes —
full disk, read-only mount, size cap reached — fails silently while the cost
reports keep rendering confident numbers from stale data. This is the one
place that says so out loud.

Emits nothing when the sink is healthy, and nothing on any error.
"""
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

    This is the failure the other hooks cannot report: they swallow it and exit
    0, so the plugin appears installed and healthy while recording nothing at
    all. Say it once, here, at SessionStart.
    """
    try:
        print(json.dumps({"systemMessage":
            "TokenDog: the hook interpreter (" + sys.executable + ") cannot "
            "import tokendog, so NO telemetry is being recorded and cost "
            "reports will read $0.00. Fix: `pip install tokendog` into that "
            "interpreter, or set TOKENDOG_PYTHON to one that has it."}))
    except Exception:
        pass
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
    try:
        from tokendog.sink import sink_health
    except Exception:
        return 0
    try:
        h = sink_health()
        if not h.get("degraded"):
            return 0
        sessions = h.get("sessions", 0)
        plural = "session" if sessions == 1 else "sessions"
        capped = "+" if h.get("sessions_capped") else ""
        msg = (f"TokenDog: telemetry sink is DEGRADED — {h.get('failures', 0)} failed "
               f"write(s) across {sessions}{capped} {plural}. Cost figures may be "
               f"incomplete. Last reason: {h.get('last_reason')}. "
               f"Run /tokendog:doctor for details.")
        print(json.dumps({"systemMessage": msg}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
