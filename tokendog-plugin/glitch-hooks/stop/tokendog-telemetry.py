#!/usr/bin/env python3
# tokendog-plugin/glitch-hooks/stop/tokendog-telemetry.py
"""Glitch `stop` hook: record AUTHORITATIVE token counts from Glitch env vars.
Install by pointing a Glitch stop hook at this script. Never raises."""
import os
import sys

def _int(name: str) -> int:
    v = os.environ.get(name, "")
    return max(0, int(v)) if str(v).strip().lstrip("-").isdigit() else 0

def main() -> int:
    try:
        from tokendog.event import TokenEvent, RUNTIME_GLITCH, now_iso
        from tokendog.sink import write_event
    except Exception:
        return 0
    try:
        event = TokenEvent(
            ts=now_iso(),
            session_id=os.environ.get("GLITCH_RUN_ID") or os.environ.get("REPO") or "glitch",
            runtime=RUNTIME_GLITCH,
            event="agent_completion",
            input_tokens=_int("TOKENS_INPUT"),
            output_tokens=_int("TOKENS_OUTPUT"),
            model=os.environ.get("MODEL"),
            agent=os.environ.get("AGENT_NAME"),
        )
        write_event(event)
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
