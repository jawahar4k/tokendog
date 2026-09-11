#!/usr/bin/env python3
"""PostToolUse hook: cap oversized tool output. Degrades to pass-through on any error.

SAFE BY DEFAULT: does nothing unless you opt in. Modes (env TOKENDOG_TRUNCATE_MODE):
  off     — (DEFAULT) do nothing; never alters output.
  shadow  — record what WOULD be saved but leave the output untouched (measure only).
  enforce — shorten oversized output (the model sees the shortened version).
Setting TOKENDOG_OBSERVE_ONLY=1 forces shadow mode.
Every truncation (enforced or shadow) is logged to the savings ledger."""
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


def _truthy(v: str | None) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")

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
    mode = os.environ.get("TOKENDOG_TRUNCATE_MODE", "off").strip().lower()
    if _truthy(os.environ.get("TOKENDOG_OBSERVE_ONLY")):
        mode = "shadow"
    if mode == "off":
        return 0
    try:
        from tokendog.condense import condense
        from tokendog.truncate import truncate_text
    except Exception:
        return 0
    try:
        out = payload.get("tool_output")
        if not isinstance(out, str) or not out:
            return 0
        max_bytes = int(os.environ.get("TOKENDOG_MAX_BYTES", "50000"))
        # Only spend a WORKER call when actually enforcing (shadow must not incur
        # cost just to measure) and only when the worker is switched on.
        worker_on = _truthy(os.environ.get("TOKENDOG_WORKER")) and mode == "enforce"
        tool_name = payload.get("tool_name") or ""
        command = (payload.get("tool_input") or {}).get("command", "") \
            if isinstance(payload.get("tool_input"), dict) else ""

        # Smart condense first (keeps the lines that matter); fall back to the
        # plain byte/line truncation if condensing found nothing to reduce.
        rep = condense(out, tool_name=tool_name, command=command, use_worker=worker_on)
        if rep["reduced"]:
            new_out, method = rep["digest"], rep["method"]
        else:
            max_lines = int(os.environ.get("TOKENDOG_MAX_LINES", "200"))
            new_out, cut = truncate_text(out, max_lines=max_lines, max_bytes=max_bytes)
            if not cut:
                return 0
            method = "truncate"

        try:
            from tokendog.savings import record_savings
            record_savings(session_id=payload.get("session_id"),
                           tool=tool_name, original=out, kept=new_out, mode=mode)
        except Exception:
            pass
        # Only enforce mode alters what the model sees; shadow just records.
        if mode == "enforce":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": new_out,
            }}))
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
