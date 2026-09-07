#!/usr/bin/env python3
"""PreToolUse/PostToolUse/Stop/SessionStart hook: tool-payload volume -> JSONL sink.

Records the approximate SIZE of each tool payload, for attribution ("which tool
produced how much text"). It does NOT record billable tokens: those bytes are
billed by the runtime on the following turn and are captured authoritatively by
`tokendog.transcripts`. Counting them here as well would double-count them, and
counting a tool RESULT as output would price it at the output rate — which is
several times the rate those bytes are actually billed at.

Never crashes the session: any failure exits 0 and emits nothing.
Requires `pip install tokendog` (the shared package)."""
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
    try:
        from tokendog.event import TokenEvent, RUNTIME_CLAUDE, SOURCE_HOOK, now_iso
        from tokendog.approx import approx_tokens
        from tokendog.sink import write_event
    except Exception:
        return 0
    try:
        hook_event = payload.get("hook_event_name", "unknown")
        payload_tokens = 0
        if hook_event == "PreToolUse":
            if "tool_input" in payload:
                payload_tokens = approx_tokens(json.dumps(payload.get("tool_input") or {}))
        elif hook_event == "PostToolUse":
            out = payload.get("tool_output")
            if out is not None:
                payload_tokens = approx_tokens(out if isinstance(out, str) else json.dumps(out))
        else:  # Stop, SessionStart
            last = payload.get("last_assistant_message")
            if last:
                payload_tokens = approx_tokens(last)
        event = TokenEvent(
            ts=now_iso(),
            session_id=payload.get("session_id", "unknown"),
            runtime=RUNTIME_CLAUDE,
            event=payload.get("hook_event_name", "unknown"),
            source=SOURCE_HOOK,
            tool_payload_tokens=payload_tokens,
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
