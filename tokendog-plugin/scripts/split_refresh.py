#!/usr/bin/env python3
"""Stop hook: refresh this session's setup/work split for the statusline.

The statusline runs under whatever `python3` is first on PATH — usually one
that cannot import tokendog — and re-renders on every keystroke, so it can
neither compute the split nor afford to parse a transcript. This hook can do
both: it runs once per assistant turn under a working interpreter, reads only
the bytes added since last time, and leaves a small JSON file behind.

Cheap by construction: on a 124 MB transcript a full parse is ~660 ms and a
resume with nothing new is ~0.1 ms.
"""
import json
import os
import sys

# Hooks run under whatever `python3` is first on PATH, which is often not the
# environment tokendog was installed into. _bootstrap re-execs us under one
# that works. Must run before stdin is read.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _bootstrap import ensure_tokendog
except Exception:  # pragma: no cover - bootstrap absent; behave as before
    def ensure_tokendog() -> bool:
        return True


def _no_tokendog() -> int:
    """No interpreter here can import tokendog.

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
        from tokendog.ledger import refresh_cache
        session_id = payload.get("session_id")
        transcript = payload.get("transcript_path")
        if session_id and transcript:
            refresh_cache(session_id, transcript)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
