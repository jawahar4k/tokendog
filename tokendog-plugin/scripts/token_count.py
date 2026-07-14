#!/usr/bin/env python3
"""PreToolUse/PostToolUse/Stop/SessionStart hook: approximate token spend -> JSONL sink.
Never crashes the session: any failure exits 0 and emits nothing.
Requires `pip install tokendog` (the shared package)."""
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
        from tokendog.event import TokenEvent, RUNTIME_CLAUDE, now_iso
        from tokendog.approx import approx_tokens
        from tokendog.sink import write_event
    except Exception:
        return 0
    try:
        hook_event = payload.get("hook_event_name", "unknown")
        input_tokens = 0
        output_tokens = 0
        if hook_event == "PreToolUse":
            if "tool_input" in payload:
                input_tokens = approx_tokens(json.dumps(payload.get("tool_input") or {}))
        elif hook_event == "PostToolUse":
            out = payload.get("tool_output")
            if out is not None:
                output_tokens = approx_tokens(out if isinstance(out, str) else json.dumps(out))
        else:  # Stop, SessionStart
            last = payload.get("last_assistant_message")
            if last:
                output_tokens = approx_tokens(last)
        event = TokenEvent(
            ts=now_iso(),
            session_id=payload.get("session_id", "unknown"),
            runtime=RUNTIME_CLAUDE,
            event=payload.get("hook_event_name", "unknown"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool=payload.get("tool_name"),
            model=payload.get("model"),
            user=os.environ.get("USER") or os.environ.get("USERNAME"),
        )
        write_event(event)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
