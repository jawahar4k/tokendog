"""Detached worker for session learnings: `python -m tokendog.learn_worker mine …`.

Hooks must return in about 100 ms, and a capture makes two model calls that
take seconds each. So a hook spawns this in its own session with the payload on
the command line and returns. TOKENDOG_LEARN_SYNC=1 runs it inline, for tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def repo_root(start) -> Path | None:
    """The working tree containing `start`, found by walking up to `.git`.

    Lessons belong to a repo, not to whichever subdirectory a session opened
    in. No git subprocess: this runs inside a hook.
    """
    try:
        d = Path(start).resolve()
    except (OSError, TypeError):
        return None
    for cand in (d, *d.parents):
        if (cand / ".git").exists():
            return cand
    return None


def spawn(*args: str) -> bool:
    """Run `learn_worker <args>` detached. True if it started (or ran inline)."""
    if os.environ.get("TOKENDOG_LEARN_SYNC"):
        return main(list(args)) == 0
    try:
        subprocess.Popen([sys.executable, "-m", "tokendog.learn_worker", *args],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return 2
    if argv[0] == "mine" and len(argv) == 4:
        from .learn_capture import capture
        capture(argv[1], argv[2], argv[3])
        return 0
    if argv[0] == "share" and len(argv) == 2:
        from .learn_share import share
        share(argv[1])
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
