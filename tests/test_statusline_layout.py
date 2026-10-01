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
    monkeypatch.setenv("HOME", str(tmp_path / "home"))     # never the real ~/.claude/plugins
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
    order = ["abc ⎇ main", "Opus 5.5", "ctx 365k/1M", "37%", "setup 75k", "≈$11.77"]
    positions = [line.index(x) for x in order]
    assert positions == sorted(positions), line


def test_folder_and_branch_lead_the_line(isolated):
    """Across several windows, where you are is what tells them apart."""
    line = plain(_load().build(_payload(isolated)))
    assert line.split(" ", 1)[1].startswith("abc ⎇ main · Opus 5.5")


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


# --- regressions from the bar: restored ---------------------------------------


def test_the_bar_warns_on_absolute_tokens_not_just_percentage():
    """365k is billed on every turn whether the window is 200k or 1M. On a 1M
    window the percentage says 37% and green; the bill says otherwise."""
    mod = _load()
    assert mod.YEL in mod.ctx_bar(37, used=365_000)
    assert mod.RED in mod.ctx_bar(45, used=450_000)
    assert mod.GRN in mod.ctx_bar(10, used=100_000)
    assert mod.RED in mod.ctx_bar(85, used=170_000)      # a small window at 85% is still red


def test_a_large_window_past_the_absolute_line_gets_a_hint(isolated):
    line = plain(_load().build(_payload(isolated)))           # 365k, no split cached
    assert "/compact" in line


def test_before_any_split_is_cached_occupancy_still_drives_the_hint(isolated):
    """The first turns of a session have no split yet. That is exactly when a
    resumed 450k window most needs saying."""
    line = plain(_load().build(_payload(isolated, context_window={
        "current_usage": {"cache_read_input_tokens": 450_000},
        "context_window_size": 1_000_000, "used_percentage": 45})))
    assert "/clear" in line


def test_a_heavy_setup_is_coloured():
    mod = _load()
    heavy = mod._setup_text(234_000, ["mcp 180k"])
    light = mod._setup_text(75_000, ["mcp 21k"])
    assert mod.YEL in heavy and mod.YEL not in light


# --- stale: a loaded session left idle -----------------------------------------


def _transcript(tmp_path, idle_h):
    import os
    t = tmp_path / "t.jsonl"
    t.write_text("{}\n")
    old = time.time() - idle_h * 3600
    os.utime(t, (old, old))
    return str(t)


def _stale_payload(isolated, idle_h, ctx):
    return _payload(isolated, transcript_path=_transcript(isolated, idle_h), context_window={
        "current_usage": {"cache_read_input_tokens": ctx},
        "context_window_size": 1_000_000, "used_percentage": ctx / 10_000})


def test_a_loaded_session_resumed_after_a_long_idle_says_clear(isolated):
    """The hygiene report's first finding: a big window left overnight costs its
    whole size again on the first message back, and clearing it is one keystroke."""
    line = plain(_load().build(_stale_payload(isolated, idle_h=14, ctx=180_000)))
    assert "→ stale, /clear" in line


def test_a_long_active_session_is_not_stale(isolated):
    """Age is not idleness: thirty hours of steady work is not left behind."""
    p = _stale_payload(isolated, idle_h=0.1, ctx=180_000)
    p["cost"]["total_duration_ms"] = 30 * 3_600_000
    assert "stale" not in plain(_load().build(p))


def test_a_small_idle_session_is_not_worth_clearing(isolated):
    assert "stale" not in plain(_load().build(_stale_payload(isolated, idle_h=40, ctx=40_000)))


def test_stale_outranks_every_other_verb(isolated):
    """Idle-and-loaded is the cheapest fix and the most expensive to ignore."""
    line = plain(_load().build(_stale_payload(isolated, idle_h=20, ctx=450_000)))
    assert "→ stale, /clear" in line and line.count("→") == 1


def test_no_transcript_means_no_stale_verdict(isolated):
    assert "stale" not in plain(_load().build(_payload(isolated)))


def test_the_stale_rule_matches_the_hygiene_report():
    """The statusline cannot import tokendog, so the thresholds are restated.
    Pinned here so "Close it now" in the report and on the line cannot drift."""
    from tokendog.hygiene import IDLE_STALE_H, STALE_MIN_CONTEXT
    mod = _load()
    assert mod.IDLE_STALE_H == IDLE_STALE_H and mod.STALE_MIN_CONTEXT == STALE_MIN_CONTEXT


def test_the_disk_baseline_uses_the_same_format_as_setup(isolated):
    d = isolated / "state"
    d.mkdir(parents=True, exist_ok=True)
    (d / "statusline_floor.json").write_text(json.dumps(
        {"sizes": {"mcp": 3_000, "skill": 1_500, "instruction": 0, "total": 4_500}}))
    seg = plain(_load().floor_segment())
    assert seg == "baseline 4.5k (mcp 3.0k, skills 1.5k)"


# --- under Glitch ---------------------------------------------------------------


def test_glitch_gets_the_line_without_claude_code_only_segments(isolated, monkeypatch):
    """Glitch runs this script as its statusLine.command. The baseline and the
    plugin-update notice describe Claude Code's setup, not the Glitch session."""
    mod = _load()
    d = isolated / "state"
    d.mkdir(parents=True, exist_ok=True)
    (d / "statusline_floor.json").write_text(json.dumps(
        {"sizes": {"mcp": 3_000, "skill": 1_500, "instruction": 0, "total": 4_500}}))
    monkeypatch.setattr(mod, "update_segment", lambda **_: "v0.1→0.2 /plugin update")
    claude = plain(mod.build(_payload(isolated)))
    glitch = plain(mod.build(_payload(isolated, runtime="glitch")))
    assert "baseline" in claude and "/plugin update" in claude
    assert "baseline" not in glitch and "/plugin update" not in glitch
    assert "abc ⎇ main" in glitch and "ctx 365k/1M" in glitch and "≈$11.77" in glitch
