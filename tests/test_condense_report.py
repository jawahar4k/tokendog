"""The condenser-savings report: projection over transcripts + recorded ledger."""
import json
from datetime import datetime, timezone

from tokendog.condense_report import projected_savings, savings_report, _result_text


def _turn(ts, ctx, tool_uses):
    return {"type": "assistant", "timestamp": ts, "uuid": f"a{ts}",
            "message": {"usage": {"input_tokens": ctx},
                        "content": [{"type": "tool_use", "id": tid, "name": name,
                                     "input": {"command": cmd}}
                                    for tid, name, cmd in tool_uses]}}


def _result(tid, text):
    return {"type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                     "content": text}]}}


def _write(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_projection_credits_reread_weighted_saving(tmp_path):
    proj_dir = tmp_path / "projects" / "-Users-x-projects-demo"
    proj_dir.mkdir(parents=True)
    big = "\n".join(f"src/f{i}.ts:{i}: TODO" for i in range(400))  # grep dump, condensable
    recs = [
        {"cwd": "/Users/x/projects/demo"},
        _turn("2026-01-01T00:00:00Z", 1000, [("t1", "Bash", "grep -rn TODO src/")]),
        _result("t1", big),
    ]
    # several later turns re-read the result (ctx keeps climbing, no reset)
    for i in range(1, 6):
        recs.append(_turn(f"2026-01-01T00:0{i}:00Z", 1000 + i * 500, []))
    _write(proj_dir / "s1.jsonl", recs)

    out = projected_savings(tmp_path / "projects", project="demo", threshold=500)
    assert out["candidates"] == 1
    # saving is (raw - digest) * turns-after; 5 later turns → strictly positive, sizeable
    assert out["saved"] > 0
    assert out["by_tool"]["Bash"]["saved"] == out["saved"]


def test_report_shape_has_projected_and_recorded(tmp_path):
    (tmp_path / "projects").mkdir()
    r = savings_report(tmp_path / "projects")
    assert set(r) >= {"projected", "recorded", "project", "window"}
    assert r["projected"]["saved"] == 0        # empty tree
    assert "events" in r["recorded"]


def test_result_text_joins_content_blocks():
    assert _result_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert _result_text("plain") == "plain"
    assert _result_text(None) == ""
