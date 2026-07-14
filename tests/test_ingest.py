import sqlite3
from tokendog.ingest import ingest_sink, ingest_glitch_firmware
from tokendog.backend import LocalSQLiteBackend, QueryFilter
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE


def test_ingest_sink(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(TokenEvent(ts="2026-07-12T00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="PostToolUse", input_tokens=7))
    b = LocalSQLiteBackend(path=":memory:")
    assert ingest_sink(b) == 1
    assert b.query(QueryFilter(group_by="runtime")).rows[0].input_tokens == 7


def _make_glitch_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE context_log (session_id TEXT, file TEXT, loaded_at TEXT, tier TEXT, tokens_used INTEGER, PRIMARY KEY (session_id, file))")
    conn.execute("INSERT INTO context_log VALUES ('g1','a.py','2026-07-12T00','hot',321)")
    conn.execute("INSERT INTO context_log VALUES ('g1','b.py','2026-07-12T01','warm',100)")
    conn.commit()
    conn.close()


def test_ingest_glitch(tmp_path):
    db = tmp_path / "firmware.db"
    _make_glitch_db(db)
    b = LocalSQLiteBackend(path=":memory:")
    assert ingest_glitch_firmware(b, db) == 2
    rollup = b.query(QueryFilter(group_by="runtime"))
    assert rollup.rows[0].key == "glitch"
    assert rollup.rows[0].input_tokens == 421


def test_ingest_glitch_missing_db(tmp_path):
    b = LocalSQLiteBackend(path=":memory:")
    assert ingest_glitch_firmware(b, tmp_path / "nope.db") == 0

def test_ingest_glitch_nonnumeric_tokens_used(tmp_path):
    db = tmp_path / "firmware.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE context_log (session_id TEXT, file TEXT, loaded_at TEXT, tier TEXT, tokens_used TEXT, PRIMARY KEY (session_id, file))")
    conn.execute("INSERT INTO context_log VALUES ('g2','x.py','2026-07-13T00','hot','N/A')")
    conn.execute("INSERT INTO context_log VALUES ('g2','y.py','2026-07-13T01','warm','200')")
    conn.commit()
    conn.close()
    b = LocalSQLiteBackend(path=":memory:")
    assert ingest_glitch_firmware(b, db) == 2
    rollup = b.query(QueryFilter(group_by="runtime"))
    assert rollup.rows[0].input_tokens == 200
