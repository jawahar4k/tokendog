"""Discovery ratio — finding vs doing."""
import json
import pytest
from tokendog.discovery import classify_bash, classify_tool, discovery_report


@pytest.mark.parametrize("cmd,kind", [
    ("grep -rn 'beta' packages/", "discovery"),
    ("rg tier --type ts", "discovery"),
    ("ls packages/cli/src", "discovery"),
    ("find . -name '*.ts'", "discovery"),
    ("cat FooterBar.tsx", "discovery"),
    ("git log --oneline -20", "discovery"),
    ("git status", "discovery"),
    ("grep -rn x . | head -50", "discovery"),      # pipe: first stage is grep
    ("npm test", "work"),
    ("pnpm build", "work"),
    ("pytest -q", "work"),
    ("git commit -m 'x'", "work"),
    ("sed -i 's/a/b/' f.ts", "work"),              # in-place edit
    ("echo hi > out.txt", "work"),                 # redirect writes
    ("mkdir -p src/new", "work"),
    ("cat template > dest.ts", "work"),            # redirect wins over cat
    ("", "other"),
    ("frobnicate --xyz", "other"),
])
def test_classify_bash(cmd, kind):
    assert classify_bash(cmd) == kind


@pytest.mark.parametrize("name,cmd,kind", [
    ("Read", "", "discovery"), ("Grep", "", "discovery"), ("Glob", "", "discovery"),
    ("Edit", "", "work"), ("Write", "", "work"), ("MultiEdit", "", "work"),
    ("Task", "", "delegate"), ("WebSearch", "", "discovery"),
    ("mcp__glitch__run_pipeline", "", "mcp"),
    ("Bash", "grep -rn x .", "discovery"), ("Bash", "npm test", "work"),
])
def test_classify_tool(name, cmd, kind):
    assert classify_tool(name, cmd) == kind


def _assistant(sid, rid, tools, cwd="/w/proj", ts="2026-09-08T10:00:00Z"):
    return {"type": "assistant", "requestId": rid, "sessionId": sid, "cwd": cwd, "timestamp": ts,
            "message": {"content": [{"type": "tool_use", "id": rid + str(i), "name": n,
                                     "input": ({"command": c} if n == "Bash" else {"file_path": "f"})}
                                    for i, (n, c) in enumerate(tools)]}}


def _write(root, name, records, sub=False):
    d = root / ("proj/subagents" if sub else "proj")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_ratio_and_flag(tmp_path):
    # 24 greps + 1 edit = 96% discovery over 25 acting calls -> flagged
    recs = [_assistant("s1", f"r{i}", [("Bash", "grep -rn x .")]) for i in range(24)]
    recs.append(_assistant("s1", "redit", [("Edit", "")]))
    _write(tmp_path, "s1", recs)
    d = discovery_report(tmp_path, min_tool_calls=20)
    r = d["sessions"][0]
    assert r["discovery"] == 24 and r["work"] == 1
    assert r["discovery_ratio"] == 96.0
    assert r["grep"] == 24
    assert r["high_discovery"] is True
    assert d["totals"]["high_discovery_sessions"] == 1


def test_not_flagged_below_min_calls(tmp_path):
    recs = [_assistant("s1", f"r{i}", [("Bash", "grep x .")]) for i in range(5)]
    _write(tmp_path, "s1", recs)
    r = discovery_report(tmp_path, min_tool_calls=20)["sessions"][0]
    assert r["discovery_ratio"] == 100.0 and r["high_discovery"] is False   # too few to flag


def test_balanced_session_not_flagged(tmp_path):
    recs = ([_assistant("s1", f"g{i}", [("Bash", "grep x .")]) for i in range(10)]
            + [_assistant("s1", f"e{i}", [("Edit", "")]) for i in range(15)])
    _write(tmp_path, "s1", recs)
    r = discovery_report(tmp_path, min_tool_calls=20)["sessions"][0]
    assert r["discovery"] == 10 and r["work"] == 15
    assert r["discovery_ratio"] == 40.0 and r["high_discovery"] is False


def test_mcp_and_delegate_excluded_from_ratio(tmp_path):
    recs = [_assistant("s1", "r0", [("Bash", "grep x ."), ("mcp__x__y", ""), ("Task", ""), ("Edit", "")])]
    _write(tmp_path, "s1", recs)
    r = discovery_report(tmp_path, min_tool_calls=1)["sessions"][0]
    assert r["discovery"] == 1 and r["work"] == 1 and r["mcp"] == 1 and r["delegate"] == 1
    assert r["discovery_ratio"] == 50.0        # ratio over find+do only (1/2)


def test_project_filter_and_overall(tmp_path):
    _write(tmp_path, "a", [_assistant("a", "r", [("Bash", "grep x .")], cwd="/w/alpha")])
    _write(tmp_path, "b", [_assistant("b", "r", [("Edit", "")], cwd="/w/beta")])
    d = discovery_report(tmp_path)
    assert d["totals"]["sessions"] == 2
    assert d["totals"]["overall_ratio"] == 50.0     # 1 find / 1 do overall
    assert discovery_report(tmp_path, project="alpha")["totals"]["sessions"] == 1


def test_gen_share_from_usage(tmp_path):
    """Gen% = output / (context + output) — the effort footprint. Deduped per
    response so the streamed copies don't multiply it."""
    recs = []
    for i in range(3):
        r = _assistant("s1", f"r{i}", [("Bash", "grep x .")])
        r["message"]["usage"] = {"cache_read_input_tokens": 100_000, "output_tokens": 500}
        recs.append(r)
        # a duplicate copy of the same response (as transcripts write per block) —
        # must NOT double-count the 100K/500.
        recs.append(dict(r))
    _write(tmp_path, "s1", recs)
    row = discovery_report(tmp_path, min_tool_calls=1)["sessions"][0]
    assert row["context_tokens"] == 300_000        # 3 responses × 100K, not 6
    assert row["output_tokens"] == 1_500           # 3 × 500
    assert row["gen_share"] == round(1500 / 301500 * 100, 2)   # tiny — effort not the cost


def test_thinking_share_when_present(tmp_path):
    r = _assistant("s1", "r0", [("Bash", "grep x .")])
    r["message"]["usage"] = {"cache_read_input_tokens": 10_000, "output_tokens": 2_000}
    r["message"]["content"].insert(0, {"type": "thinking", "thinking": "word " * 400})
    _write(tmp_path, "s1", [r])
    row = discovery_report(tmp_path, min_tool_calls=1)["sessions"][0]
    assert row["thinking_tokens"] > 0
    assert row["thinking_share"] is not None
