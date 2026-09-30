#!/usr/bin/env python3
"""PreCompact / SessionEnd hook: mine this session for lessons, in the background.

Returns immediately. The work — two model calls that take seconds — runs in a
detached worker, so no session ever waits on it. Does nothing inside a
learning worker's own session, which would otherwise mine itself forever.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _bootstrap import ensure_tokendog
except Exception:  # pragma: no cover - bootstrap absent; behave as before
    def ensure_tokendog() -> bool:
        return True


def main() -> int:
    if os.environ.get("TOKENDOG_LEARN_CHILD"):
        return 0
    if not ensure_tokendog():
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        from tokendog.learn_capture import _enabled
        from tokendog.learn_worker import repo_root, spawn
        if not _enabled():
            return 0
        session_id, transcript = payload.get("session_id"), payload.get("transcript_path")
        repo = repo_root(payload.get("cwd") or os.getcwd())
        if session_id and transcript and repo:
            spawn("mine", str(session_id), str(transcript), str(repo))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
