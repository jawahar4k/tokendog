from __future__ import annotations
import posixpath
import sqlite3
from pathlib import Path
from .event import TokenEvent, RUNTIME_GLITCH, SOURCE_GLITCH
from .sink import read_events
from .transcripts import read_transcripts


def redact_path(value: str | None) -> str | None:
    """Reduce a filesystem path to its basename before it is recorded.

    A full path is PII in ways a basename is not — it leaks project names,
    the user's home directory name, and sometimes customer names. Redaction
    happens here, at the emitter, so every consumer of the sink inherits the
    same privacy posture instead of each re-deciding it.
    """
    if not value:
        return value
    return posixpath.basename(str(value).replace("\\", "/").rstrip("/")) or None


def ingest_sink(backend) -> int:
    n = 0
    for ev in read_events():
        backend.ingest(ev)
        n += 1
    return n


def ingest_transcripts(backend, root=None) -> int:
    """Load authoritative per-turn usage from Claude Code transcripts."""
    n = 0
    for ev in read_transcripts(root):
        backend.ingest(ev)
        n += 1
    return n


def ingest_glitch_firmware(backend, db_path) -> int:
    path = Path(db_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path))
    n = 0
    try:
        try:
            cur = conn.execute(
                "SELECT session_id, file, loaded_at, tier, tokens_used FROM context_log")
        except sqlite3.OperationalError:
            return 0
        for session_id, file, loaded_at, tier, tokens_used in cur.fetchall():
            backend.ingest(TokenEvent(
                ts=loaded_at or "",
                session_id=session_id or "unknown",
                runtime=RUNTIME_GLITCH,
                event="context-load",
                source=SOURCE_GLITCH,
                input_tokens=int(tokens_used) if str(tokens_used or "").isdigit() else 0,
                output_tokens=0,
                tool=f"context:{tier}" if tier else "context",
                file=redact_path(file),
            ))
            n += 1
    finally:
        conn.close()
    return n
