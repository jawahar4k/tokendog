import json, importlib.util, io, sys
from pathlib import Path
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "session_summary.py"

def _load():
    spec = importlib.util.spec_from_file_location("session_summary", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_summary_emitted(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(TokenEvent(ts="2026-07-13T00:00:00+00:00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="PostToolUse", input_tokens=500_000, model="sonnet"))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "s"})))
    rc = mod.main(); out = capsys.readouterr().out
    assert rc == 0
    assert "TokenDog" in json.loads(out)["systemMessage"]

def test_zero_spend_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "s"})))
    assert mod.main() == 0 and capsys.readouterr().out.strip() == ""
