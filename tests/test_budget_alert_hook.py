import json, importlib.util, io, sys
from pathlib import Path
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE, SOURCE_TRANSCRIPT
from tokendog import budget
from datetime import datetime, timezone

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "budget_alert.py"

def _load():
    spec = importlib.util.spec_from_file_location("budget_alert", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_posts_when_over_alert(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_event(TokenEvent(ts=today+"T00:00:00+00:00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="assistant-turn", source=SOURCE_TRANSCRIPT,
                           input_tokens=1_000_000, model="sonnet"))
    budget.save_budget(budget.Budget(alert_usd=1.0, webhook_url="http://hook.local/x"))
    mod = _load()
    posted = {}
    monkeypatch.setattr(mod, "_post", lambda url, payload: posted.update({"url": url, "payload": payload}))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "s"})))
    assert mod.main() == 0
    assert posted["url"] == "http://hook.local/x"
    assert "text" in posted["payload"]

def test_no_post_without_webhook(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    budget.save_budget(budget.Budget(alert_usd=1.0))  # no webhook_url
    mod = _load()
    called = {"n": 0}
    monkeypatch.setattr(mod, "_post", lambda url, payload: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "s"})))
    assert mod.main() == 0 and called["n"] == 0
