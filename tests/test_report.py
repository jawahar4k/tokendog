from tokendog import report
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(TokenEvent(ts="2026-07-12T00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="PostToolUse", tool="Read", input_tokens=10, output_tokens=4))


def test_cost_summary_shape(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    s = report.cost_summary(group_by="runtime")
    assert s["group_by"] == "runtime"
    assert s["rows"][0]["key"] == "claude-code"
    assert s["rows"][0]["input_tokens"] == 10


def test_format_rollup_is_markdown(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = report.format_rollup(report.cost_summary())
    assert "| Group" in out and "claude-code" in out and "approxim" in out.lower()


def test_doctor_mentions_home(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    txt = report.doctor_report(str(tmp_path))
    assert str(tmp_path) in txt and "tiktoken" in txt.lower()


def test_cli_cost(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    rc = report.main(["cost", "--group-by", "tool"])
    assert rc == 0
    assert "Read" in capsys.readouterr().out
