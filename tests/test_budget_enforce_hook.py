import json, importlib.util, io, sys
from pathlib import Path
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE, SOURCE_TRANSCRIPT
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
                           event="assistant-turn", source=SOURCE_TRANSCRIPT,
                           input_tokens=1_000_000, model="sonnet"))
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


def _over(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_event(TokenEvent(ts=today+"T00:00:00+00:00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="assistant-turn", source=SOURCE_TRANSCRIPT,
                           input_tokens=1_000_000, model="sonnet"))
    budget.save_budget(budget.Budget(daily_usd=1.0))


def test_the_budget_command_itself_is_never_denied(tmp_path, monkeypatch, capsys):
    """A cap that refuses the call which raises it is a trap: once crossed, the
    slash command that fixes it is itself a tool call and gets refused too."""
    _over(tmp_path, monkeypatch)
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash",
        "tool_input": {"command": "python3 /x/scripts/tokendog_cli.py budget --set-daily 1500"}})))
    assert mod.main() == 0 and capsys.readouterr().out.strip() == ""


def test_other_commands_are_still_denied_when_over(tmp_path, monkeypatch, capsys):
    _over(tmp_path, monkeypatch)
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"}})))
    mod.main()
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_reason_names_a_way_out_that_works(tmp_path, monkeypatch, capsys):
    """The old reason said "raise it with /tokendog:budget" — which was also denied."""
    _over(tmp_path, monkeypatch)
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "PreToolUse", "session_id": "s"})))
    mod.main()
    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "/tokendog:budget" in reason and "TOKENDOG_OBSERVE_ONLY" in reason


def test_the_deny_says_which_cap_tripped_and_that_a_session_cap_never_clears(tmp_path, monkeypatch, capsys):
    """A session cap counts the session's whole life, with no date filter, so
    once crossed it stays crossed: a new day does not help and resuming keeps
    the same id. The reason has to say so, or the reader waits for a reset that
    is never coming."""
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_event(TokenEvent(ts=today+"T00:00:00+00:00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="assistant-turn", source=SOURCE_TRANSCRIPT,
                           input_tokens=1_000_000, model="sonnet"))
    budget.save_budget(budget.Budget(session_usd=0.5))     # session only
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "PreToolUse", "session_id": "s"})))
    mod.main()
    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "session" in reason.lower()
    assert "whole session" in reason or "does not reset" in reason
    # Don't report a figure for a cap that is not set: "today $X of $None" is noise.
    assert "today $" not in reason
