import json
from tokendog import savings


def test_record_and_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    big = "\n".join(str(i) for i in range(1000))
    small = "short"
    savings.record_savings(session_id="s1", tool="Bash", original=big, kept=small, mode="enforce")
    savings.record_savings(session_id="s1", tool="Read", original=big, kept=small, mode="shadow")

    s = savings.savings_summary()
    assert s["events"] == 2
    assert s["total_saved"] > 0
    assert s["total_original"] > s["total_saved"]
    assert 0 < s["pct"] <= 100
    assert set(s["per_tool"]) == {"Bash", "Read"}
    assert s["modes"] == {"enforce": 1, "shadow": 1}


def test_ledger_is_separate_from_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    savings.record_savings(session_id="s", tool="Bash", original="x" * 999, kept="x", mode="enforce")
    # savings live in savings.jsonl, never in the telemetry roll-up
    assert (tmp_path / "savings.jsonl").exists()
    assert not list((tmp_path / "telemetry").glob("*.jsonl")) if (tmp_path / "telemetry").exists() else True


def test_record_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    # missing tool / session and empty strings must not blow up
    savings.record_savings(session_id=None, tool=None, original="", kept="", mode="enforce")


def test_empty_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    s = savings.savings_summary()
    assert s == {"events": 0, "total_saved": 0, "total_original": 0, "pct": 0.0,
                 "per_tool": {}, "per_session": {}, "modes": {}}
