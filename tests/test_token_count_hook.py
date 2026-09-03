import json, importlib.util, io, sys
from pathlib import Path
from tokendog.sink import read_events
from tokendog.event import SOURCE_HOOK

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "token_count.py"

def _load():
    spec = importlib.util.spec_from_file_location("token_count", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _run(mod, payload, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return mod.main()

def test_posttooluse_records_event(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    rc = _run(mod, {"hook_event_name": "PostToolUse", "session_id": "s1",
                    "tool_name": "Read", "tool_input": {"file_path": "a.py"},
                    "tool_output": "line1\nline2\n" * 50}, monkeypatch)
    assert rc == 0
    events = list(read_events())
    assert len(events) == 1
    e = events[0]
    assert e.runtime == "claude-code" and e.event == "PostToolUse"
    assert e.tool == "Read" and e.tool_payload_tokens > 0

def test_bad_stdin_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json{{"))
    assert mod.main() == 0
    assert list(read_events()) == []

def test_pretooluse_measures_tool_input(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    rc = _run(mod, {"hook_event_name": "PreToolUse", "session_id": "s2",
                    "tool_name": "Write", "tool_input": {"file_path": "b.py", "content": "x" * 100}},
              monkeypatch)
    assert rc == 0
    events = list(read_events())
    assert len(events) == 1
    assert events[0].tool_payload_tokens > 0

def test_posttooluse_measures_tool_output(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    rc = _run(mod, {"hook_event_name": "PostToolUse", "session_id": "s3",
                    "tool_name": "Read",
                    "tool_input": {"file_path": "c.py"},
                    "tool_output": "result " * 50},
              monkeypatch)
    assert rc == 0
    events = list(read_events())
    assert len(events) == 1
    assert events[0].tool_payload_tokens > 0

def test_hook_never_writes_billable_token_fields(tmp_path, monkeypatch):
    """Regression: PostToolUse used to write the tool RESULT into
    output_tokens, which billed it at the output rate ($25/MTok on Opus) even
    though those bytes are input-side context the transcript already bills."""
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    payload = {"tool_input": {"file_path": "d.py"}, "tool_output": "line\n" * 100}
    _run(mod, {**payload, "hook_event_name": "PreToolUse", "session_id": "s4"}, monkeypatch)
    _run(mod, {**payload, "hook_event_name": "PostToolUse", "session_id": "s4"}, monkeypatch)
    events = list(read_events())
    assert len(events) == 2
    for e in events:
        assert e.source == SOURCE_HOOK
        assert not e.is_authoritative
        assert e.input_tokens == 0
        assert e.output_tokens == 0
        assert e.cache_read_tokens == 0
        assert e.cache_creation_tokens == 0
        assert e.tool_payload_tokens > 0
