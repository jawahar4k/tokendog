import json, importlib.util, io, sys
from pathlib import Path
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE
from tokendog import budget
from datetime import datetime, timezone

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "budget_enforce.py"

def _load():
    spec = importlib.util.spec_from_file_location("budget_enforce", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_deny_when_over_daily(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_event(TokenEvent(ts=today+"T00:00:00+00:00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="PostToolUse", input_tokens=1_000_000, model="sonnet"))
    budget.save_budget(budget.Budget(daily_usd=1.0))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "PreToolUse", "session_id": "s"})))
    rc = mod.main(); out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"

def test_no_deny_when_under(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    budget.save_budget(budget.Budget(daily_usd=1000.0))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "PreToolUse", "session_id": "s"})))
    assert mod.main() == 0 and capsys.readouterr().out.strip() == ""

def test_error_does_not_block(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO("garbage"))
    assert mod.main() == 0 and capsys.readouterr().out.strip() == ""
