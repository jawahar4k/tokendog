"""The intervention ledger.

`savings` only ever measured output truncation, so with truncation off it read
0.0% forever — indistinguishable from "we measured and nothing worked". These
tests pin the difference: a flat result must be reported as a flat result, not
as an absence of measurement.
"""
import json

from tokendog import effect, report


def _assistant(tool, tool_id, tool_input=None, *, ts="2026-09-06T00:00:00+00:00",
               cwd="/w/alpha", usage=None):
    return json.dumps({
        "timestamp": ts, "sessionId": "s", "cwd": cwd, "type": "assistant",
        "message": {"model": "sonnet", "usage": usage or {"output_tokens": 1},
                    "content": [{"type": "tool_use", "id": tool_id,
                                 "name": tool, "input": tool_input or {}}]}})


def _result(tool_id, body, *, ts="2026-09-06T00:00:01+00:00", cwd="/w/alpha"):
    return json.dumps({
        "timestamp": ts, "sessionId": "s", "cwd": cwd, "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                 "content": body}]}})


def _write(tmp_path, *lines, name="s.jsonl", project="-w-alpha"):
    d = tmp_path / project
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("\n".join(lines))
    return tmp_path


def test_joins_tool_use_to_its_result(tmp_path):
    root = _write(tmp_path, _assistant("Bash", "t1"), _result("t1", "x" * 500))
    calls = list(effect.iter_tool_calls(root))
    assert len(calls) == 1
    assert calls[0].tool == "Bash" and calls[0].result_bytes == 500


def test_tool_use_without_a_result_is_not_counted(tmp_path):
    """An unanswered call returned no bytes; counting it would dilute the mean."""
    root = _write(tmp_path, _assistant("Bash", "t1"))
    assert list(effect.iter_tool_calls(root)) == []


def test_scoping_is_only_judged_where_the_record_can_answer_it(tmp_path):
    """Charging a tool with no scoping parameters as unscoped invents
    non-compliance, so those report None rather than False."""
    root = _write(tmp_path,
                  _assistant("Read", "a", {"file_path": "/f"}),
                  _result("a", "x"),
                  _assistant("Read", "b", {"file_path": "/f", "limit": 20}),
                  _result("b", "x"),
                  _assistant("Bash", "c", {"command": "ls"}),
                  _result("c", "x"))
    by_id = {c.tool + str(i): c for i, c in enumerate(effect.iter_tool_calls(root))}
    scoped = [c.scoped for c in effect.iter_tool_calls(root)]
    assert scoped == [False, True, None]


def test_summary_reports_mean_and_scoping_share(tmp_path):
    root = _write(tmp_path,
                  _assistant("Read", "a", {"limit": 5}), _result("a", "y" * 100),
                  _assistant("Read", "b", {}), _result("b", "y" * 300))
    s = effect.tool_summary(effect.iter_tool_calls(root))
    assert s["tools"]["Read"]["mean_bytes"] == 200
    assert s["tools"]["Read"]["scoped_pct"] == 50.0


def test_project_scoping(tmp_path):
    root = _write(tmp_path, _assistant("Bash", "a", cwd="/w/alpha"),
                  _result("a", "x" * 10))
    _write(tmp_path, _assistant("Bash", "b", cwd="/w/beta"), _result("b", "x" * 99),
           name="t.jsonl", project="-w-beta")
    assert len(list(effect.iter_tool_calls(root, project="alpha"))) == 1
    assert len(list(effect.iter_tool_calls(root, project="beta"))) == 1


def test_position_curve_prices_the_compounding(tmp_path):
    """A late turn re-reads everything accumulated, so it costs a multiple of
    an early one. That multiple is what makes 'split this stage' rankable."""
    lines = []
    for i in range(120):
        lines.append(json.dumps({
            "timestamp": "2026-09-06T00:00:00+00:00", "sessionId": "s",
            "cwd": "/w/alpha", "type": "assistant",
            "message": {"model": "sonnet",
                        "usage": {"cache_read_input_tokens": 1000 * (i + 1)}}}))
    root = _write(tmp_path, *lines)
    curve = effect.position_curve(root)
    labels = [b["label"] for b in curve["buckets"]]
    assert labels == ["1-10", "11-25", "26-50", "51-100", "101+"]
    costs = [b["cost_per_turn"] for b in curve["buckets"]]
    assert costs == sorted(costs), "cost must rise with turn position"
    assert curve["ratio"] > 1


def test_flat_result_is_reported_as_flat_not_missing(tmp_path, monkeypatch):
    """The whole point: 'we changed something and nothing happened' has to be
    visible, and distinguishable from 'we never looked'."""
    root = _write(tmp_path,
                  _assistant("Bash", "a", ts="2026-09-01T00:00:00+00:00"),
                  _result("a", "x" * 1000, ts="2026-09-01T00:00:01+00:00"),
                  _assistant("Bash", "b", ts="2026-09-09T00:00:00+00:00"),
                  _result("b", "x" * 1000, ts="2026-09-09T00:00:01+00:00"))
    monkeypatch.setenv("TOKENDOG_TRANSCRIPT_ROOT", str(root))
    out = report.format_effect(report.effect_data(split="2026-09-06"))
    assert "no measurable change" in out
    assert "+0.0%" in out or "0.0%" in out


def test_improvement_is_reported_as_improvement(tmp_path, monkeypatch):
    root = _write(tmp_path,
                  _assistant("Bash", "a", ts="2026-09-01T00:00:00+00:00"),
                  _result("a", "x" * 1000, ts="2026-09-01T00:00:01+00:00"),
                  _assistant("Bash", "b", ts="2026-09-09T00:00:00+00:00"),
                  _result("b", "x" * 200, ts="2026-09-09T00:00:01+00:00"))
    monkeypatch.setenv("TOKENDOG_TRANSCRIPT_ROOT", str(root))
    out = report.format_effect(report.effect_data(split="2026-09-06"))
    assert "smaller payloads" in out and "-80" in out


def test_cli_effect_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TOKENDOG_TRANSCRIPT_ROOT", str(tmp_path / "empty"))
    assert report.main(["effect"]) == 0
    assert "intervention ledger" in capsys.readouterr().out
