from tokendog import report, budget
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE
from datetime import datetime, timezone


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_budget_set_and_show(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert report.main(["budget", "--set-daily", "10", "--set-alert", "5"]) == 0
    assert budget.load_budget().daily_usd == 10.0
    report.main(["budget", "--show"])
    assert "10" in capsys.readouterr().out


def test_audit_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert report.main(["audit"]) == 0


def test_audit_session_header_and_session_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(TokenEvent(
        ts=_today() + "T00:00:00+00:00",
        session_id="sess-abc123",
        runtime=RUNTIME_CLAUDE,
        event="PostToolUse",
        input_tokens=500,
        output_tokens=100,
        model="sonnet",
    ))
    assert report.main(["audit", "--session", "sess-abc123"]) == 0
    out = capsys.readouterr().out
    assert "by tool" not in out
    assert "session_id" in out
    assert "sess-abc123" in out
