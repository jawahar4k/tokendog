import importlib.util
from pathlib import Path
from tokendog.sink import read_events

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "glitch-hooks" / "stop" / "tokendog-telemetry.py"

def _load():
    spec = importlib.util.spec_from_file_location("glitch_stop", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_records_authoritative_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENS_INPUT", "1234")
    monkeypatch.setenv("TOKENS_OUTPUT", "567")
    monkeypatch.setenv("MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("AGENT_NAME", "developer-ai")
    mod = _load()
    assert mod.main() == 0
    events = list(read_events())
    assert len(events) == 1
    e = events[0]
    assert e.runtime == "glitch" and e.input_tokens == 1234 and e.output_tokens == 567
    assert e.model == "claude-sonnet-4-6" and e.agent == "developer-ai"

def test_missing_env_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    for k in ("TOKENS_INPUT", "TOKENS_OUTPUT", "MODEL", "AGENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    mod = _load()
    assert mod.main() == 0  # zero-token event still written or skipped, never raises
