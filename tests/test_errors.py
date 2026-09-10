"""Per-tool error rates, matched from transcripts."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tokendog.errors import categorise, tool_errors

T0 = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("text,cat", [
    ("Error: operation timed out after 30s", "timeout"),
    ("bash: no such file or directory", "not-found"),
    ("EACCES: permission denied", "permission"),
    ("connect ECONNREFUSED 127.0.0.1:5432", "network"),
    ("HTTP 429 too many requests", "rate-limit"),
    ("SyntaxError: unexpected token", "syntax"),
    ("the user interrupted the request", "interrupted"),
    ("Command failed with exit code 2", "nonzero-exit"),
    ("something weird happened", "other"),
    ("", "other"),
])
def test_categorise(text, cat):
    assert categorise(text) == cat


def _use(rid, name):
    return {"type": "assistant", "requestId": rid, "cwd": "/w/proj",
            "timestamp": T0.isoformat().replace("+00:00", "Z"),
            "message": {"content": [{"type": "tool_use", "id": rid + "-t", "name": name}]}}


def _result(rid, *, error=False, text="ok", at=T0):
    return {"type": "user", "timestamp": at.isoformat().replace("+00:00", "Z"),
            "cwd": "/w/proj",
            "message": {"content": [{"type": "tool_result", "tool_use_id": rid + "-t",
                                     "is_error": error, "content": text}]}}


def _write(root, name, records):
    d = root / "proj"; d.mkdir(exist_ok=True)
    (d / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n",
                                     encoding="utf-8")


def test_error_rate_per_tool(tmp_path):
    recs = []
    for i in range(8):
        recs += [_use(f"b{i}", "Bash"), _result(f"b{i}", error=(i < 2), text="exit code 1")]
    for i in range(3):
        recs += [_use(f"r{i}", "Read"), _result(f"r{i}")]
    _write(tmp_path, "s1", recs)
    d = tool_errors(tmp_path)
    by = {r["tool"]: r for r in d["tools"]}
    assert by["Bash"]["calls"] == 8 and by["Bash"]["errors"] == 2
    assert by["Bash"]["error_rate"] == 25.0
    assert by["Read"]["errors"] == 0
    assert d["totals"]["calls"] == 11 and d["totals"]["errors"] == 2


def test_high_error_flag_needs_volume_and_rate(tmp_path):
    # a tool called twice, once failing (50%) is NOT flagged — too few calls
    recs = [_use("a0", "Flaky"), _result("a0", error=True, text="boom"),
            _use("a1", "Flaky"), _result("a1")]
    _write(tmp_path, "s1", recs)
    assert tool_errors(tmp_path)["tools"][0]["high_error"] is False


def test_high_error_flag_fires_on_a_real_pattern(tmp_path):
    recs = []
    for i in range(6):
        recs += [_use(f"x{i}", "mcp__thing__do"), _result(f"x{i}", error=(i < 3), text="timed out")]
    _write(tmp_path, "s1", recs)
    r = tool_errors(tmp_path)["tools"][0]
    assert r["high_error"] is True and r["dominant"] == "timeout"


def test_dominant_category_is_the_most_common(tmp_path):
    recs = [
        _use("a", "T"), _result("a", error=True, text="timed out"),
        _use("b", "T"), _result("b", error=True, text="timed out"),
        _use("c", "T"), _result("c", error=True, text="permission denied"),
    ]
    _write(tmp_path, "s1", recs)
    assert tool_errors(tmp_path)["tools"][0]["dominant"] == "timeout"


def test_a_result_with_no_matching_use_is_counted_as_unknown(tmp_path):
    _write(tmp_path, "s1", [_result("orphan", error=True, text="boom")])
    by = {r["tool"]: r for r in tool_errors(tmp_path)["tools"]}
    assert "unknown" in by and by["unknown"]["errors"] == 1


def test_window_scopes_by_result_time(tmp_path):
    recs = [
        _use("old", "Bash"), _result("old", error=True, text="x", at=T0),
        _use("new", "Bash"), _result("new", error=True, text="x", at=T0 + timedelta(hours=5)),
    ]
    _write(tmp_path, "s1", recs)
    from tokendog.window import Window
    w = Window(since=T0 + timedelta(hours=1))
    d = tool_errors(tmp_path, window=w)
    assert d["totals"]["calls"] == 1


def test_project_filter(tmp_path):
    d1 = tmp_path / "proj"; d1.mkdir()
    r_a = [_use("a", "Bash"), _result("a", error=True, text="x")]
    for r in r_a:
        r["cwd"] = "/w/alpha"
    (d1 / "a.jsonl").write_text("\n".join(json.dumps(r) for r in r_a) + "\n")
    r_b = [_use("b", "Bash"), _result("b", error=True, text="x")]
    for r in r_b:
        r["cwd"] = "/w/beta"
    (d1 / "b.jsonl").write_text("\n".join(json.dumps(r) for r in r_b) + "\n")
    d = tool_errors(tmp_path, project="alpha")
    assert d["totals"]["calls"] == 1


def test_empty_root(tmp_path):
    assert tool_errors(tmp_path / "nope")["totals"]["calls"] == 0
