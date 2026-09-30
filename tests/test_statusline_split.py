"""The statusline's setup/work segment, and the prompts keyed on work."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "statusline.py"


def _load():
    spec = importlib.util.spec_from_file_location("statusline", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")            # compare text, not escapes
    return tmp_path


def _cache(home, session, *, setup, work):
    d = home / "split"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session}.json").write_text(json.dumps({"split": {
        "setup": setup, "work": work,
        "setup_total": sum(setup.values()), "work_total": sum(work.values()),
        "total": sum(setup.values()) + sum(work.values()),
        "ratio": 2.1, "turns": 10, "segments": 1}}), encoding="utf-8")


SESSION = "abcd1234-0000-4000-8000-abcdefabcdef"


def test_the_segment_names_the_parts_worth_naming(home):
    mod = _load()
    _cache(home, SESSION,
           setup={"sys": 26_000, "mcp": 31_000, "skills": 13_000, "agents": 300},
           work={"tools": 90_000, "chat": 5_000, "other": 0,
                 "mcp_results": 0, "skill_results": 0})
    import re
    seg = re.sub(r"\x1b\[[0-9;]*m", "", mod.split_segment({"session_id": SESSION}))
    assert seg == "setup 70k (sys 26k, mcp 31k, skills 13k)"
    # Parts too small to act on are noise in a one-line statusline, and the
    # work half drives the hint but is not printed.
    assert "agents" not in seg and "work" not in seg


def test_no_cache_means_no_segment_not_a_crash(home):
    """A session that has not finished a turn yet, or a machine where the hook
    cannot run. The line still renders."""
    assert _load().split_segment({"session_id": SESSION}) is None


def test_a_corrupt_cache_is_ignored(home):
    mod = _load()
    d = home / "split"; d.mkdir(parents=True)
    (d / f"{SESSION}.json").write_text("{not json", encoding="utf-8")
    assert mod.split_segment({"session_id": SESSION}) is None


def test_the_segment_can_be_switched_off(home, monkeypatch):
    mod = _load()
    _cache(home, SESSION, setup={"sys": 30_000, "mcp": 0, "skills": 0, "agents": 0},
           work={"tools": 0, "chat": 0, "other": 0, "mcp_results": 0, "skill_results": 0})
    monkeypatch.setenv("TOKENDOG_STATUSLINE_SPLIT", "0")
    assert mod.split_segment({"session_id": SESSION}) is None


# --- the prompt: which lever, not just "full" -------------------------------


def test_a_heavy_history_is_told_to_clear(home):
    """Keyed on work, not on percentage: this is the one case /clear fixes."""
    mod = _load()
    _cache(home, SESSION, setup={"sys": 30_000, "mcp": 0, "skills": 0, "agents": 0},
           work={"tools": 450_000, "chat": 0, "other": 0,
                 "mcp_results": 0, "skill_results": 0})
    assert mod.lever_hint({"session_id": SESSION}) == "→ /clear or /compact"


def test_a_heavy_setup_is_not_told_to_clear(home):
    """/clear re-injects setup, so telling someone to clear a session whose
    weight is a connector they never call wastes the only working state they
    have and changes nothing."""
    mod = _load()
    _cache(home, SESSION, setup={"sys": 40_000, "mcp": 300_000, "skills": 0, "agents": 0},
           work={"tools": 20_000, "chat": 0, "other": 0,
                 "mcp_results": 0, "skill_results": 0})
    hint = mod.lever_hint({"session_id": SESSION})
    assert hint is not None and "clear" not in hint
    assert "floor" in hint


def test_a_fresh_compaction_in_a_big_window_says_nothing(home):
    """A 1M window at 45% used to trip a percentage rule. Nothing is wrong here."""
    mod = _load()
    _cache(home, SESSION, setup={"sys": 40_000, "mcp": 20_000, "skills": 0, "agents": 0},
           work={"tools": 60_000, "chat": 0, "other": 0,
                 "mcp_results": 0, "skill_results": 0})
    assert mod.lever_hint({"session_id": SESSION}) is None
