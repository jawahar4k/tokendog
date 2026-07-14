# tests/test_config.py
from pathlib import Path
from tokendog import config

def test_home_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert config.tokendog_home() == tmp_path

def test_telemetry_dir_created(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    d = config.telemetry_dir()
    assert d == tmp_path / "telemetry"
    assert d.is_dir()

def test_default_home_when_unset(monkeypatch):
    monkeypatch.delenv("TOKENDOG_HOME", raising=False)
    assert config.tokendog_home() == Path.home() / ".tokendog"
