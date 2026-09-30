"""The statusline's layout: what is on the line, in what order, and what is not."""
import importlib.util
import json
import re
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "statusline.py"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SESSION = "abcd1234-0000-4000-8000-abcdefabcdef"


def _load():
    spec = importlib.util.spec_from_file_location("statusline_layout", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def plain(s):
    return ANSI.sub("", s or "")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "state"))
    for k in ("COLUMNS", "TOKENDOG_STATUSLINE_COLS"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _split(tmp_path, **setup):
    d = tmp_path / "state" / "split"
    d.mkdir(parents=True, exist_ok=True)
    s = {"sys": 34_000, "mcp": 21_000, "skills": 14_000, "agents": 6_000} | setup
    (d / f"{SESSION}.json").write_text(json.dumps({"split": {
        "setup": s, "work": {"tools": 250_000, "chat": 40_000, "other": 0,
                             "mcp_results": 0, "skill_results": 0},
        "setup_total": sum(s.values()), "work_total": 290_000, "total": sum(s.values()) + 290_000,
        "ratio": 2.1, "turns": 40, "segments": 1}}))


def _payload(tmp_path, **extra):
    repo = tmp_path / "abc"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    p = {"session_id": SESSION, "workspace": {"current_dir": str(repo)},
         "model": {"display_name": "Opus 5.5"},
         "context_window": {"current_usage": {"cache_read_input_tokens": 365_000},
                            "context_window_size": 1_000_000, "used_percentage": 37},
         "cost": {"total_cost_usd": 11.77, "total_duration_ms": 30 * 3_600_000},
         "rate_limits": {"five_hour": {"used_percentage": 70},
                         "seven_day": {"used_percentage": 70}}}
    p.update(extra)
    return p


# --- what is on the line -----------------------------------------------------


def test_the_line_reads_in_the_reference_order(isolated):
    _split(isolated)
    line = plain(_load().build(_payload(isolated)))
    order = ["Opus 5.5", "abc:main", "ctx 365k/1M", "37%", "setup 75k", "≈$11.77"]
    positions = [line.index(x) for x in order]
    assert positions == sorted(positions), line


def test_folder_and_branch_are_joined_with_a_colon(isolated):
    line = plain(_load().build(_payload(isolated)))
    assert "abc:main" in line


def test_setup_parts_sit_in_brackets_separated_by_commas(isolated):
    """Commas instead of ` · ` inside the bracket: the dot is the segment
    separator, and reusing it inside one segment reads as four segments."""
    _split(isolated)
    line = plain(_load().build(_payload(isolated)))
    assert "setup 75k (sys 34k, mcp 21k, skills 14k, agents 6k)" in line


def test_counts_use_a_lowercase_k(isolated):
    line = plain(_load().build(_payload(isolated)))
    assert "365k" in line and "365K" not in line


# --- what is deliberately not on the line ------------------------------------


@pytest.mark.parametrize("gone", ["age ", "5h ", "7d ", "work "])
def test_segments_that_did_not_earn_their_space_are_gone(isolated, gone):
    """Session age, the 5h and 7d windows, and the work total were removed after
    use: none of them changed what anyone did next. The split's work half still
    drives the /clear hint; it just is not printed."""
    _split(isolated)
    line = plain(_load().build(_payload(isolated)))
    assert gone not in line, line


# --- the context bar ---------------------------------------------------------


@pytest.mark.parametrize("pct,filled", [(0, 0), (17, 1), (37, 2), (50, 3), (99, 6), (100, 6)])
def test_the_bar_fills_with_the_window(pct, filled):
    bar = plain(_load().ctx_bar(pct))
    assert len(bar) == 6
    assert bar.count("▓") == filled


def test_the_bar_is_green_then_yellow_then_red():
    mod = _load()
    assert mod.GRN in mod.ctx_bar(30)
    assert mod.YEL in mod.ctx_bar(60)
    assert mod.RED in mod.ctx_bar(85)


def test_compact_is_suggested_at_eighty_percent(isolated):
    line = plain(_load().build(_payload(isolated, context_window={
        "current_usage": {"cache_read_input_tokens": 820_000},
        "context_window_size": 1_000_000, "used_percentage": 82})))
    assert "/compact" in line


def test_a_missing_percentage_is_computed_from_the_window(isolated):
    line = plain(_load().build(_payload(isolated, context_window={
        "current_usage": {"cache_read_input_tokens": 100_000},
        "context_window_size": 200_000})))
    assert "50%" in line


# --- lessons -----------------------------------------------------------------


def _status(repo, **fields):
    d = repo / ".claude" / "learnings" / "_local"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".status.json").write_text(json.dumps(fields))


def test_a_repo_where_learning_never_ran_shows_nothing(isolated):
    assert _load().lessons_segment(_payload(isolated)) is None


def test_new_lessons_are_counted_with_their_age(isolated):
    _status(isolated / "abc", state="done", added=2, at=time.time() * 1000 - 5 * 60_000)
    seg = plain(_load().lessons_segment(_payload(isolated)))
    assert seg == "learned 2 lessons (5m ago)"


def test_one_lesson_is_singular(isolated):
    _status(isolated / "abc", state="done", added=1, at=time.time() * 1000 - 60_000)
    assert plain(_load().lessons_segment(_payload(isolated))) == "learned 1 lesson (1m ago)"


def test_nothing_new_still_says_when_it_last_looked(isolated):
    _status(isolated / "abc", state="skipped", added=0, at=time.time() * 1000 - 3 * 86_400_000)
    assert plain(_load().lessons_segment(_payload(isolated))) == "no new lessons (3d ago)"


def test_a_running_capture_says_so(isolated):
    _status(isolated / "abc", state="running", at=time.time() * 1000)
    assert "learning" in plain(_load().lessons_segment(_payload(isolated)))


def test_a_failed_capture_is_visible(isolated):
    """A capture that fails silently looks exactly like one that found nothing."""
    _status(isolated / "abc", state="failed", at=time.time() * 1000 - 60_000)
    assert "failed" in plain(_load().lessons_segment(_payload(isolated)))


def test_the_lessons_segment_is_on_the_line(isolated):
    _status(isolated / "abc", state="skipped", added=0, at=time.time() * 1000 - 3 * 86_400_000)
    line = plain(_load().build(_payload(isolated)))
    assert "no new lessons (3d ago)" in line
    assert line.index("no new lessons") < line.index("≈$")
