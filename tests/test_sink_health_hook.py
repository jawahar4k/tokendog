import json, importlib.util, io, sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "sink_health.py"


def _load():
    spec = importlib.util.spec_from_file_location("sink_health_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, payload, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return mod.main()


def test_silent_when_sink_is_healthy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    assert _run(mod, {"hook_event_name": "SessionStart", "session_id": "s"}, monkeypatch) == 0
    assert capsys.readouterr().out == ""


def test_warns_when_sink_is_degraded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    from tokendog.sink import record_failure
    record_failure("s1", "No space left on device")
    record_failure("s2", "No space left on device")
    mod = _load()
    assert _run(mod, {"hook_event_name": "SessionStart", "session_id": "s3"}, monkeypatch) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    msg = payload["systemMessage"]
    assert "DEGRADED" in msg
    assert "2 failed write(s)" in msg
    assert "2 sessions" in msg
    assert "No space left" in msg


def test_never_crashes_on_bad_stdin(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json{{"))
    assert mod.main() == 0
    assert capsys.readouterr().out == ""


def test_registered_as_a_session_start_hook():
    manifest = json.loads(
        (HOOK.parents[1] / "hooks" / "hooks.json").read_text())
    cmds = [h["command"] for grp in manifest["hooks"]["SessionStart"] for h in grp["hooks"]]
    assert any("sink_health.py" in c for c in cmds)


def test_doctor_reports_sink_health(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    from tokendog import report
    from tokendog.sink import record_failure
    assert "sink health: ok" in report.doctor_report(str(tmp_path))
    record_failure("s", "No space left on device")
    txt = report.doctor_report(str(tmp_path))
    assert "sink health: DEGRADED" in txt and "No space left" in txt
