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
