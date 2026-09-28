from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import tokendog_home

# Save the whole output before cutting any of it.
#
# This is what separates condensing from truncating. A digest that drops the
# middle of a log has destroyed it: the model cannot ask for the part it turns
# out to need, and neither can the reader. Writing the full text to disk first
# and naming the omitted line ranges turns the cut into a deferral — the same
# move the read guard makes, one layer later.
#
# Tool output is often a log, and logs contain credentials. It goes somewhere
# only this user can read, or it does not go at all.

RETENTION_DAYS = 7
DIR_MODE = 0o700
FILE_MODE = 0o600

_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def spill_dir(home=None) -> Path:
    base = (Path(home) if home is not None else tokendog_home()) / "output"
    base.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    # mkdir's mode is masked by umask, so set it explicitly on a directory that
    # may already exist from an earlier, more permissive run.
    os.chmod(base, DIR_MODE)
    return base


@dataclass
class Spill:
    """Where a full tool output was parked, and how to ask for a piece of it."""

    path: str
    lines: int

    def pointer(self, *, kept_ranges: list[tuple[int, int]]) -> str:
        """One line telling the model where the rest is and which parts they are.

        Line ranges, not a vague "some output was omitted": the model can act on
        `Read(offset=11, limit=49)` and cannot act on an apology.
        """
        gaps = omitted_ranges(self.lines, kept_ranges)
        if not gaps:
            return (f"[TokenDog: full output ({self.lines:,} lines) saved to {self.path} — "
                    "nothing omitted]")
        shown = ", ".join(f"{a}-{b}" for a, b in gaps[:12])
        if len(gaps) > 12:
            shown += f", and {len(gaps) - 12} more"
        return (f"[TokenDog condensed this. Full output ({self.lines:,} lines) is at "
                f"{self.path}; lines {shown} are not above. Read it with offset/limit to see "
                "any of them.]")


def omitted_ranges(total_lines: int, kept: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The 1-based inclusive ranges that `kept` does not cover."""
    if total_lines <= 0:
        return []
    merged: list[list[int]] = []
    for start, end in sorted(kept):
        start, end = max(1, start), min(total_lines, end)
        if start > end:
            continue
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    gaps, cursor = [], 1
    for start, end in merged:
        if start > cursor:
            gaps.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= total_lines:
        gaps.append((cursor, total_lines))
    return gaps


def save(session_id, text: str, *, home=None) -> Spill | None:
    """Park the full output. None if it cannot be written — never raises.

    A caller that gets None must not cut anything: no spill, no pointer, and a
    digest with no way back is the truncation this replaces.
    """
    if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
        return None
    try:
        base = spill_dir(home) / session_id
        base.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        os.chmod(base, DIR_MODE)
        path = base / f"{int(time.time())}-{uuid.uuid4().hex[:8]}.txt"
        # Create with the mode set from the start: a file that is briefly
        # world-readable is world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        return None
    return Spill(str(path), text.count("\n") + (0 if text.endswith("\n") or not text else 1))


def prune(home=None, *, days: int = RETENTION_DAYS) -> int:
    """Delete spills older than `days`. Returns how many went."""
    cutoff = time.time() - days * 86400
    gone = 0
    try:
        base = spill_dir(home)
    except OSError:
        return 0
    for path in base.rglob("*.txt"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                gone += 1
        except OSError:
            continue
    for child in base.iterdir() if base.is_dir() else []:
        try:
            if child.is_dir() and not any(child.iterdir()):
                child.rmdir()
        except OSError:
            continue
    return gone
