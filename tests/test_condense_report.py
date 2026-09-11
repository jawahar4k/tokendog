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


# --- replay: the quality check --------------------------------------------


def _write_transcript(root, *payloads):
    """One session: each payload is a Bash grep result, followed by later turns
    that re-read them (so the re-read weight is non-zero)."""
    proj = root / "projects" / "-Users-x-projects-demo"
    proj.mkdir(parents=True, exist_ok=True)
    recs = [{"cwd": "/Users/x/projects/demo"}]
    uses = [(f"t{i}", "Bash", "grep -rn TODO src/") for i in range(len(payloads))]
    recs.append(_turn("2026-01-01T00:00:00Z", 1000, uses))
    for (tid, _, _), text in zip(uses, payloads):
        recs.append(_result(tid, text))
    for i in range(1, 6):
        recs.append(_turn(f"2026-01-01T00:0{i}:00Z", 1000 + i * 500, []))
    _write(proj / "s1.jsonl", recs)
    return root / "projects"



def test_dropped_lines_reports_what_the_digest_lost():
    from tokendog.condense_report import dropped_lines
    raw = "keep1\nlose1\nlose2\nkeep2"
    assert dropped_lines(raw, "keep1\nkeep2") == ["lose1", "lose2"]


def test_dropped_lines_is_empty_when_nothing_went_missing():
    from tokendog.condense_report import dropped_lines
    assert dropped_lines("a\nb", "a\nb\n… [footer]") == []


def test_replay_returns_real_cases_worst_first(tmp_path):
    """A savings total cannot show whether the dropped lines mattered; this can."""
    from tokendog.condense_report import replay
    big = "\n".join(f"line {i} of a long log" for i in range(900))
    small = "\n".join(f"row {i}" for i in range(400))
    root = _write_transcript(tmp_path, big, small)

    out = replay(root, limit=5)
    assert out["condensable"] >= 1
    c = out["cases"][0]
    assert c["digest_tokens"] < c["raw_tokens"]
    assert c["dropped_lines"] > 0
    assert c["dropped_sample"], "a case with nothing to show is not reviewable"
    saved = [x["saved"] for x in out["cases"]]
    assert saved == sorted(saved, reverse=True)


def test_replay_limit_is_respected(tmp_path):
    from tokendog.condense_report import replay
    root = _write_transcript(tmp_path, *["\n".join(f"line {i}" for i in range(900))] * 3)
    assert len(replay(root, limit=1)["cases"]) == 1
