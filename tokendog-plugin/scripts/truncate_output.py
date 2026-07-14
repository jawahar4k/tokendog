#!/usr/bin/env python3
"""PostToolUse hook: cap oversized tool output. Degrades to pass-through on any error."""
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
        from tokendog.truncate import truncate_text
    except Exception:
        return 0
    try:
        out = payload.get("tool_output")
        if not isinstance(out, str) or not out:
            return 0
        max_lines = int(os.environ.get("TOKENDOG_MAX_LINES", "200"))
        max_bytes = int(os.environ.get("TOKENDOG_MAX_BYTES", "50000"))
        new_out, cut = truncate_text(out, max_lines=max_lines, max_bytes=max_bytes)
        if cut:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": new_out,
            }}))
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
