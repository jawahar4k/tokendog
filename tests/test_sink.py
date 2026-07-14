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
