#!/usr/bin/env python3
"""SessionStart hook: load this repo's lessons into the session as notes.

Runs on every SessionStart source, including after /clear and a compaction,
because both throw away whatever was loaded before. Also starts a share in the
background when one is due and sharing is switched on.
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
        from tokendog.learn import inject_text
        from tokendog.learn_capture import _enabled
        from tokendog.learn_worker import repo_root, spawn
        if not _enabled():
            return 0
        repo = repo_root(payload.get("cwd") or os.getcwd())
        if repo is None:
            return 0
        text = inject_text(repo)
        if text:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": text}}))
        if payload.get("source") != "compact":
            try:
                from tokendog.learn_share import share_due
                if share_due(repo):
                    spawn("share", str(repo))
            except Exception:
                pass
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
