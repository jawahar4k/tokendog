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
    assert "| Group" in out and "claude-code" in out
    # The four billing buckets are shown, not one collapsed total.
    assert "Cache write" in out and "Cache read" in out
    # And the provenance of the cost figure is stated.
    assert "authoritative" in out.lower()


def test_doctor_mentions_home(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    txt = report.doctor_report(str(tmp_path))
    assert str(tmp_path) in txt and "tiktoken" in txt.lower()


def test_cli_cost(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    rc = report.main(["cost", "--group-by", "tool"])
    assert rc == 0
    assert "Read" in capsys.readouterr().out


def test_audit_shows_payload_volume_not_structural_zeros(tmp_path, monkeypatch, capsys):
    """`audit` grouped by tool and rendered the PRICED columns.

    A transcript turn is where the cost is and carries no tool name; the hook
    events that do name a tool are never priced, because their bytes are billed
    by the turn that follows. So every tool row read $0.00 with all-zero token
    columns — structural zeros that look exactly like measured ones — while the
    payload volume the hooks had recorded went unshown.
    """
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    from tokendog.event import SOURCE_HOOK
    write_event(TokenEvent(ts="2026-09-07T00:00:00+00:00", session_id="s",
                           runtime=RUNTIME_CLAUDE, event="PostToolUse",
                           source=SOURCE_HOOK, tool="Bash",
                           tool_payload_tokens=900))
    write_event(TokenEvent(ts="2026-09-07T00:00:01+00:00", session_id="s",
                           runtime=RUNTIME_CLAUDE, event="PostToolUse",
                           source=SOURCE_HOOK, tool="Read",
                           tool_payload_tokens=100))
    assert report.main(["audit"]) == 0
    out = capsys.readouterr().out
    assert "Payload tokens" in out
    assert "900" in out and "90.0%" in out
    # and it must not imply a per-tool price exists
    assert "$0.0000" not in out
    assert "no per-tool dollar figure" in out.lower()
