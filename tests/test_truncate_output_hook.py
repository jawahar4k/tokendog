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

def test_large_output_truncated(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENDOG_MAX_LINES", "50")
    mod = _load()
    big = "\n".join(str(i) for i in range(1000))
    rc, out = _run(mod, {"hook_event_name": "PostToolUse", "tool_output": big, "tool_name": "Bash"}, monkeypatch, capsys)
    assert rc == 0
    data = json.loads(out)
    assert data["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "truncated" in data["hookSpecificOutput"]["updatedToolOutput"]
    # enforce mode records a savings entry
    from tokendog.savings import savings_summary
    s = savings_summary()
    assert s["events"] == 1 and s["total_saved"] > 0 and s["modes"] == {"enforce": 1}


def test_shadow_mode_records_but_does_not_modify(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENDOG_MAX_LINES", "50")
    monkeypatch.setenv("TOKENDOG_OBSERVE_ONLY", "1")
    mod = _load()
    big = "\n".join(str(i) for i in range(1000))
    rc, out = _run(mod, {"hook_event_name": "PostToolUse", "tool_output": big, "tool_name": "Bash"}, monkeypatch, capsys)
    assert rc == 0
    assert out.strip() == ""  # output NOT modified
    from tokendog.savings import savings_summary
    s = savings_summary()
    assert s["events"] == 1 and s["total_saved"] > 0 and s["modes"] == {"shadow": 1}


def test_mode_off_does_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENDOG_MAX_LINES", "50")
    monkeypatch.setenv("TOKENDOG_TRUNCATE_MODE", "off")
    mod = _load()
    big = "\n".join(str(i) for i in range(1000))
    rc, out = _run(mod, {"hook_event_name": "PostToolUse", "tool_output": big, "tool_name": "Bash"}, monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""
    from tokendog.savings import savings_summary
    assert savings_summary()["events"] == 0

def test_small_output_no_stdout(monkeypatch, capsys):
    mod = _load()
    rc, out = _run(mod, {"hook_event_name": "PostToolUse", "tool_output": "small"}, monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""

def test_bad_stdin_safe(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert mod.main() == 0
