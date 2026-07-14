from __future__ import annotations
import sqlite3
from pathlib import Path
from .event import TokenEvent, RUNTIME_GLITCH
from .sink import read_events


def ingest_sink(backend) -> int:
    n = 0
    for ev in read_events():
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
                input_tokens=int(tokens_used) if str(tokens_used or "").isdigit() else 0,
                output_tokens=0,
                tool=f"context:{tier}" if tier else "context",
                file=file,
            ))
            n += 1
    finally:
        conn.close()
    return n
