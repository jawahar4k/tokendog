from tokendog import budget
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE
from datetime import datetime, timezone


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seed(tmp_path, monkeypatch, itok, otok, model="sonnet", session="s"):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(TokenEvent(ts=_today()+"T00:00:00+00:00", session_id=session,
                           runtime=RUNTIME_CLAUDE, event="PostToolUse",
                           input_tokens=itok, output_tokens=otok, model=model))


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    budget.save_budget(budget.Budget(daily_usd=5.0, alert_usd=2.0, webhook_url="http://x"))
    b = budget.load_budget()
    assert b.daily_usd == 5.0 and b.alert_usd == 2.0 and b.webhook_url == "http://x"


def test_load_missing_is_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert budget.load_budget() == budget.Budget()


def test_spend_and_check_over_daily(tmp_path, monkeypatch):
    # 1,000,000 input tokens @ sonnet ($3/M) = $3.00
    _seed(tmp_path, monkeypatch, 1_000_000, 0)
    budget.save_budget(budget.Budget(daily_usd=1.0, alert_usd=2.0))
    result = budget.check(session_id="s")
    assert result["daily"] >= 3.0
    assert result["over_daily"] is True
    assert result["over_alert"] is True


def test_spend_session_unknown_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert budget.spend_session("nonexistent-session-id") == 0.0
