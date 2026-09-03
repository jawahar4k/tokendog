from tokendog.sink import write_event, read_events
from tokendog.event import TokenEvent, RUNTIME_CLAUDE

def _ev(i):
    return TokenEvent(ts="2026-07-12T00:00:0%d+00:00" % i, session_id="s",
                      runtime=RUNTIME_CLAUDE, event="PostToolUse", input_tokens=i)

def test_write_then_read(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(_ev(1)); write_event(_ev(2))
    got = list(read_events())
    assert [e.input_tokens for e in got] == [1, 2]

def test_read_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert list(read_events()) == []

def test_corrupt_line_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(_ev(3))
    # inject a corrupt line directly after the valid one
    from tokendog.config import telemetry_dir
    jf = sorted(telemetry_dir().glob("*.jsonl"))[0]
    with jf.open("a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    write_event(_ev(4))
    got = list(read_events())
    assert [e.input_tokens for e in got] == [3, 4]


# --- retention ---------------------------------------------------------

def test_prunes_files_older_than_retention(tmp_path, monkeypatch):
    from tokendog.config import telemetry_dir
    from tokendog import sink
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENDOG_RETENTION_DAYS", "7")
    d = telemetry_dir(); d.mkdir(parents=True, exist_ok=True)
    old = d / "2020-01-01.jsonl"; old.write_text("{}\n", encoding="utf-8")
    recent = d / (sink._today() + ".jsonl"); recent.write_text("", encoding="utf-8")
    assert sink.prune_old_files(force=True) == 1
    assert not old.exists() and recent.exists()


def test_prune_leaves_non_daily_files_alone(tmp_path, monkeypatch):
    from tokendog.config import telemetry_dir
    from tokendog import sink
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    d = telemetry_dir(); d.mkdir(parents=True, exist_ok=True)
    keep = d / "notes.jsonl"; keep.write_text("{}\n", encoding="utf-8")
    sink.prune_old_files(force=True)
    assert keep.exists()


# --- size cap ----------------------------------------------------------

def test_size_cap_stops_unbounded_growth(tmp_path, monkeypatch):
    from tokendog import sink
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENDOG_MAX_SINK_MB", "1")
    monkeypatch.setattr(sink, "max_sink_bytes", lambda: 200)
    for i in range(20):
        sink.write_event(_ev(i % 10))
    path = sink.telemetry_dir() / (sink._today() + ".jsonl")
    assert path.stat().st_size < 1000  # capped, not unbounded
    assert sink.sink_health()["degraded"] is True


# --- degradation is visible, not silent --------------------------------

def test_write_failure_is_recorded_not_swallowed(tmp_path, monkeypatch):
    """The hooks swallow every exception so they can never crash a session.
    That is exactly why an unwritable sink has to leave a trace somewhere —
    otherwise it stops silently and the reports keep looking confident."""
    from tokendog import sink
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    # Make today's sink file unwritable by putting a directory in its place.
    blocked = sink.telemetry_dir() / (sink._today() + ".jsonl")
    blocked.mkdir(parents=True, exist_ok=True)

    assert sink.write_event(_ev(1)) is None
    h = sink.sink_health()
    assert h["degraded"] is True and h["failures"] == 1
    assert h["last_reason"]


def test_failures_accumulate_across_sessions(tmp_path, monkeypatch):
    from tokendog import sink
    from tokendog.event import TokenEvent, RUNTIME_CLAUDE
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    for sid in ("s1", "s2", "s2"):
        sink.record_failure(sid, "disk full")
    h = sink.sink_health()
    assert h["failures"] == 3
    assert h["sessions"] == 2  # distinct sessions affected


def test_successful_write_clears_health(tmp_path, monkeypatch):
    from tokendog import sink
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    sink.record_failure("s", "transient")
    assert sink.sink_health()["degraded"] is True
    sink.write_event(_ev(1))
    assert sink.sink_health()["degraded"] is False


def test_health_never_raises_on_corrupt_state(tmp_path, monkeypatch):
    from tokendog import sink
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    sink.health_path().parent.mkdir(parents=True, exist_ok=True)
    sink.health_path().write_text("{not json", encoding="utf-8")
    assert sink.sink_health()["degraded"] is False
    sink.record_failure("s", "x")  # must not raise
    assert sink.sink_health()["failures"] == 1


def test_write_event_still_returns_path_on_success(tmp_path, monkeypatch):
    from tokendog import sink
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert sink.write_event(_ev(1)) is not None
