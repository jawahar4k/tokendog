import json, importlib.util, io, sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "truncate_output.py"

def _load():
    spec = importlib.util.spec_from_file_location("truncate_output", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def _run(mod, payload, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    rc = mod.main()
    return rc, capsys.readouterr().out

def test_large_output_truncated(monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_MAX_LINES", "50")
    mod = _load()
    big = "\n".join(str(i) for i in range(1000))
    rc, out = _run(mod, {"hook_event_name": "PostToolUse", "tool_output": big}, monkeypatch, capsys)
    assert rc == 0
    data = json.loads(out)
    assert data["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "truncated" in data["hookSpecificOutput"]["updatedToolOutput"]

def test_small_output_no_stdout(monkeypatch, capsys):
    mod = _load()
    rc, out = _run(mod, {"hook_event_name": "PostToolUse", "tool_output": "small"}, monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""

def test_bad_stdin_safe(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert mod.main() == 0
