#!/usr/bin/env python3
"""Stop hook: emit a one-line token-spend summary for the session."""
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
        msg = (f"TokenDog: this session ~${spend:.4f} across {calls} recorded tool calls"
               f"{qualifier}. Run /tokendog:cost for the full breakdown.")
        print(json.dumps({"systemMessage": msg}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
