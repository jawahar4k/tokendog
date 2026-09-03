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


# --- redaction ---------------------------------------------------------

def test_redact_path_keeps_only_basename():
    from tokendog.ingest import redact_path
    # A full path leaks the project name, the user's home directory name, and
    # sometimes a customer name. A basename does not.
    assert redact_path("/Users/alice/projects/acme-corp/src/secret_pricing.py") == "secret_pricing.py"
    assert redact_path("C:\\Users\\alice\\work\\client.md") == "client.md"
    assert redact_path("/trailing/dir/") == "dir"
    assert redact_path(None) is None
    assert redact_path("") == ""


def test_glitch_ingest_redacts_file_paths(tmp_path):
    import sqlite3
    from tokendog.backend import LocalSQLiteBackend
    from tokendog.ingest import ingest_glitch_firmware
    db = tmp_path / "firmware.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE context_log (session_id TEXT, file TEXT, loaded_at TEXT, tier TEXT, tokens_used INT)")
    conn.execute("INSERT INTO context_log VALUES ('s', '/Users/alice/acme/secret.py', '2026-07-12T00', 'hot', '10')")
    conn.commit(); conn.close()

    b = LocalSQLiteBackend(path=":memory:")
    assert ingest_glitch_firmware(b, db) == 1
    stored = b._conn.execute("SELECT file FROM events").fetchone()[0]
    assert stored == "secret.py"
    assert "alice" not in stored and "acme" not in stored


# --- transcript ingestion ----------------------------------------------

def test_ingest_transcripts_prices_authoritative_turns(tmp_path):
    import json
    from tokendog.backend import LocalSQLiteBackend, QueryFilter
    from tokendog.ingest import ingest_transcripts
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "s.jsonl").write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-09-01T10:00:00Z", "sessionId": "s",
        "message": {"model": "claude-opus-5", "usage": {
            "input_tokens": 2, "output_tokens": 289,
            "cache_read_input_tokens": 20920,
            "cache_creation_input_tokens": 17943,
            "cache_creation": {"ephemeral_1h_input_tokens": 17943,
                               "ephemeral_5m_input_tokens": 0},
        }},
    }), encoding="utf-8")

    b = LocalSQLiteBackend(path=":memory:")
    assert ingest_transcripts(b, tmp_path) == 1
    row = b.query(QueryFilter(group_by="runtime")).rows[0]
    assert row.cache_read_tokens == 20920
    assert row.cache_creation_tokens == 17943
    assert row.est_cost_usd > 0
