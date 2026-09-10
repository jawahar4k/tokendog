"""Turn-by-turn: what entered a window at each turn, and what it then cost."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tokendog.session_detail import (
    baseline_breakdown,
    find_transcript,
    read_turns,
    session_detail,
)

T0 = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


def _resp(rid, minutes, ctx, *, out=100, tool=None, result=None, blocks=None):
    """One response record, plus the tool_result record that answers it."""
    content = []
    for b in (blocks or []):
        content.append({"type": b, "thinking": "x " * 20} if b == "thinking"
                        else {"type": b, "text": "y " * 20})
    if tool:
        content.append({"type": "tool_use", "id": rid + "-t", "name": tool,
                        "input": {"command": "cat big-file.ts"}})
    recs = [{
        "type": "assistant", "requestId": rid, "cwd": "/x/demo",
        "entrypoint": "sdk-cli",
        "timestamp": (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
        "message": {"model": "m", "content": content,
                    "usage": {"input_tokens": 0, "output_tokens": out,
                              "cache_read_input_tokens": ctx,
                              "cache_creation_input_tokens": 0}},
    }]
    if tool:
        recs.append({"type": "user", "timestamp": recs[0]["timestamp"],
                     "message": {"content": [{"type": "tool_result",
                                              "tool_use_id": rid + "-t",
                                              "content": "z" * (result or 400)}]}})
    return recs


def _write(root, name, groups):
    d = root / "proj"
    d.mkdir(exist_ok=True)
    f = d / f"{name}.jsonl"
    lines = [json.dumps(r) for g in groups for r in g]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


# --- finding it --------------------------------------------------------


def test_a_unique_prefix_is_enough(tmp_path):
    _write(tmp_path, "abcdef12-rest", [_resp("r1", 0, 1000)])
    assert find_transcript("abcdef12", tmp_path) is not None


def test_an_ambiguous_prefix_finds_nothing(tmp_path):
    """Better to refuse than to drill into whichever file sorted first."""
    _write(tmp_path, "aa11", [_resp("r1", 0, 1000)])
    _write(tmp_path, "aa22", [_resp("r2", 0, 1000)])
    assert find_transcript("aa", tmp_path) is None


def test_an_unknown_id_is_reported_not_raised(tmp_path):
    d = session_detail("nope", root=tmp_path)
    assert d["found"] is False and "no transcript" in d["error"]


def test_a_transcript_with_no_metered_turns_is_reported(tmp_path):
    f = tmp_path / "proj"; f.mkdir()
    (f / "empty.jsonl").write_text(json.dumps({"type": "user"}) + "\n", encoding="utf-8")
    assert session_detail("empty", root=tmp_path)["found"] is False


# --- one response, one turn -------------------------------------------


def test_a_response_written_as_several_records_is_one_turn(tmp_path):
    """Same grouping as the ingest: records repeat their usage."""
    recs = _resp("r1", 0, 5000, tool="Bash")
    recs.insert(1, dict(recs[0], message=dict(recs[0]["message"], content=[])))
    _write(tmp_path, "s1", [recs])
    rows, _, _ = read_turns(tmp_path / "proj" / "s1.jsonl")
    assert len(rows) == 1


def test_project_and_entrypoint_come_off_the_records(tmp_path):
    _write(tmp_path, "s1", [_resp("r1", 0, 5000)])
    d = session_detail("s1", root=tmp_path)
    assert d["project"] == "demo" and d["headless"] is True


# --- deltas and carry --------------------------------------------------


def test_the_first_turn_is_charged_its_whole_occupancy(tmp_path):
    """It is the floor every later turn re-reads, so it is not free."""
    _write(tmp_path, "s1", [_resp("r1", 0, 10_000), _resp("r2", 1, 12_000)])
    t = session_detail("s1", root=tmp_path)["turns"]
    assert t[0]["delta"] == 10_000
    assert t[0]["carried"] == 10_000 * 1


def test_a_delta_is_the_growth_since_the_previous_turn(tmp_path):
    _write(tmp_path, "s1", [_resp("r1", 0, 10_000), _resp("r2", 1, 25_000)])
    assert session_detail("s1", root=tmp_path)["turns"][1]["delta"] == 15_000


def test_carry_counts_only_the_turns_that_followed(tmp_path):
    _write(tmp_path, "s1", [_resp("r1", 0, 1_000), _resp("r2", 1, 3_000),
                            _resp("r3", 2, 4_000)])
    t = session_detail("s1", root=tmp_path)["turns"]
    assert [x["turns_after"] for x in t] == [2, 1, 0]
    assert t[-1]["carried"] == 0, "the last turn is re-read by nothing"


def test_carry_stops_at_a_reset(tmp_path):
    """Charging past a reset bills turns that never saw the content."""
    _write(tmp_path, "s1", [_resp("r1", 0, 500_000), _resp("r2", 1, 20_000),
                            _resp("r3", 2, 30_000), _resp("r4", 3, 40_000)])
    d = session_detail("s1", root=tmp_path)
    first = d["turns"][0]
    assert first["turns_after"] == 0, "its segment ended at the reset"
    assert len(d["segments"]) == 2
    assert d["turns"][1]["segment"] == 2 and d["turns"][1]["reset_before"] is True


def test_total_carried_stays_bounded_by_what_was_carried(tmp_path):
    _write(tmp_path, "s1", [_resp(f"r{i}", i, 10_000 * (i + 1)) for i in range(6)])
    d = session_detail("s1", root=tmp_path)
    assert d["totals"]["carried"] <= d["totals"]["input"]


# --- attribution -------------------------------------------------------


def test_a_tool_result_is_counted_against_the_turn_that_called_it(tmp_path):
    _write(tmp_path, "s1", [_resp("r1", 0, 5_000, tool="Bash", result=4_000),
                            _resp("r2", 1, 9_000)])
    t = session_detail("s1", root=tmp_path)["turns"][0]
    assert t["tools"][0]["name"] == "Bash"
    assert t["tool_tokens"] > 0
    assert "cat big-file.ts" in t["tools"][0]["detail"]


def test_unattributable_growth_is_reported_as_residual(tmp_path):
    """Not folded into whichever category happens to be nearest."""
    _write(tmp_path, "s1", [_resp("r1", 0, 5_000), _resp("r2", 1, 90_000)])
    t = session_detail("s1", root=tmp_path)["turns"][1]
    assert t["residual"] > 0


def test_tools_are_rolled_up_across_the_session(tmp_path):
    _write(tmp_path, "s1", [_resp("r1", 0, 5_000, tool="Bash"),
                            _resp("r2", 1, 9_000, tool="Bash"),
                            _resp("r3", 2, 12_000, tool="Edit")])
    by = {r["tool"]: r for r in session_detail("s1", root=tmp_path)["by_tool"]}
    assert by["Bash"]["calls"] == 2 and by["Edit"]["calls"] == 1


def test_turns_are_ranked_by_what_they_cost_not_by_size(tmp_path):
    _write(tmp_path, "s1", [_resp("r1", 0, 1_000)]
           + [_resp(f"r{i}", i, 1_000 + 100 * i) for i in range(2, 8)]
           + [_resp("rBig", 9, 200_000)])
    top = session_detail("s1", root=tmp_path)["top_turns"]
    assert top[0]["carried"] >= top[-1]["carried"]
    assert top[0]["index"] == 1, "the floor, re-read by everything, leads"


# --- the baseline ------------------------------------------------------


def test_the_floor_is_split_only_as_far_as_the_data_allows(tmp_path):
    b = baseline_breakdown(30_000, home=tmp_path, inventory={"tools": {"mcp__a__b": 2_000}})
    assert b["tool_schemas"] == 2_000
    assert b["remainder"] == 30_000 - 2_000
    assert "system prompt" in b["remainder_note"]


def test_without_an_inventory_the_schemas_sit_in_the_remainder(tmp_path):
    b = baseline_breakdown(30_000, home=tmp_path, inventory={})
    assert b["tool_schemas"] is None
    assert b["remainder"] == 30_000
    assert b["have_inventory"] is False


def test_the_remainder_never_goes_negative(tmp_path):
    b = baseline_breakdown(100, home=tmp_path, inventory={"tools": {"mcp__a__b": 9_999}})
    assert b["remainder"] == 0


# --- contract ----------------------------------------------------------


def test_the_drilldown_is_json_serialisable(tmp_path):
    _write(tmp_path, "s1", [_resp("r1", 0, 5_000, tool="Bash")])
    d = session_detail("s1", root=tmp_path)
    assert json.loads(json.dumps(d))["found"] is True


# --- windows -----------------------------------------------------------


def _window(from_min, to_min=None):
    from tokendog.window import Window
    return Window(since=T0 + timedelta(minutes=from_min),
                  until=(T0 + timedelta(minutes=to_min)
                         if to_min is not None else None))


def _five_turns(tmp_path):
    return _write(tmp_path, "win12345-rest", [
        _resp("r1", 0, 20_000),
        _resp("r2", 10, 40_000, tool="Bash"),
        _resp("r3", 20, 60_000, tool="Bash"),
        _resp("r4", 30, 80_000, tool="Read"),
        _resp("r5", 40, 100_000),
    ])


def test_a_window_marks_turns_rather_than_removing_them(tmp_path):
    """Every per-turn figure is measured against the whole session — a turn's
    carry cost is its size times the turns that re-read it, and those exist
    outside any window the reader asks about."""
    _five_turns(tmp_path)
    d = session_detail("win12345", root=tmp_path, window=_window(15, 35))
    assert len(d["turns"]) == 5
    assert [t["in_window"] for t in d["turns"]] == [False, False, True, True, False]


def test_the_window_gets_its_own_totals(tmp_path):
    _five_turns(tmp_path)
    d = session_detail("win12345", root=tmp_path, window=_window(15, 35))
    assert d["window_totals"]["turns"] == 2
    assert d["window_totals"]["input"] == 60_000 + 80_000
    assert d["totals"]["turns"] == 5


def test_the_baseline_is_taken_from_the_real_first_turn_not_the_windows(tmp_path):
    """Otherwise a window opening mid-session reports a carried context as the
    floor every turn pays — a number that is not a baseline at all."""
    _five_turns(tmp_path)
    windowed = session_detail("win12345", root=tmp_path, window=_window(15, 35))
    plain = session_detail("win12345", root=tmp_path)
    assert windowed["baseline"]["total"] == plain["baseline"]["total"] == 20_000


def test_the_costliest_turns_are_ranked_within_the_window(tmp_path):
    """It is the list the reader opened the window to get."""
    _five_turns(tmp_path)
    d = session_detail("win12345", root=tmp_path, window=_window(15, 35))
    assert {t["index"] for t in d["top_turns"]} == {3, 4}


def test_the_tool_rollup_is_scoped_to_the_window(tmp_path):
    _five_turns(tmp_path)
    d = session_detail("win12345", root=tmp_path, window=_window(15, 25))
    assert [r["tool"] for r in d["by_tool"]] == ["Bash"]
    assert d["by_tool"][0]["calls"] == 1


def test_no_window_leaves_every_turn_in_scope(tmp_path):
    _five_turns(tmp_path)
    d = session_detail("win12345", root=tmp_path)
    assert all(t["in_window"] for t in d["turns"])
    assert d["window"] is None and d["window_totals"] is None


def test_a_window_that_catches_nothing_reports_zero_rather_than_failing(tmp_path):
    _five_turns(tmp_path)
    d = session_detail("win12345", root=tmp_path, window=_window(500, 600))
    assert d["window_totals"]["turns"] == 0
    assert d["window_totals"]["burn_per_min"] is None
    assert d["top_turns"] == []


def test_the_window_label_travels_with_the_payload(tmp_path):
    _five_turns(tmp_path)
    d = session_detail("win12345", root=tmp_path, window=_window(15, 35))
    assert "→" in d["window"]


def test_burn_is_reported_for_the_session_and_the_window(tmp_path):
    _five_turns(tmp_path)
    d = session_detail("win12345", root=tmp_path, window=_window(15, 35))
    # Whole session: 300k over 40 minutes. Window: 140k over 10.
    assert d["totals"]["burn_per_min"] == pytest.approx(300_000 / 40)
    assert d["window_totals"]["burn_per_min"] == pytest.approx(140_000 / 10)


def test_the_instant_is_kept_off_the_serialised_turn(tmp_path):
    """`at` is the serialised form; two spellings of one timestamp in a payload
    invite the reader to pick the wrong one."""
    _five_turns(tmp_path)
    d = session_detail("win12345", root=tmp_path)
    assert "at_dt" not in d["turns"][0]


def test_detail_does_not_publish_an_absolute_path(tmp_path):
    """`/session/<id>.json` is explicitly meant to be handed to other tools, so
    whatever it carries travels. An absolute transcript path carries the home
    directory and the encoded project directory with it — the same leak
    `transcripts.project_of` returns a basename to avoid."""
    _write(tmp_path, "s1", [_resp("r1", 0, 1000)])
    d = session_detail("s1", root=tmp_path)
    assert d["found"]
    assert d["path"] == "s1.jsonl"
    assert "/" not in d["path"]
    assert str(tmp_path) not in json.dumps(d)
