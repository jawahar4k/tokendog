"""Statusline segments ported from the notes: where, cache, spend, band, width."""
import importlib.util
import os
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "statusline.py"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _load():
    spec = importlib.util.spec_from_file_location("statusline_segments", SCRIPT)
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


# --- folder ------------------------------------------------------------------


def test_folder_is_the_workspace_basename():
    mod = _load()
    seg = mod.where_segment({"workspace": {"current_dir": "/Users/x/projects/acme-api"}})
    assert "acme-api" in plain(seg)
    assert "/Users" not in plain(seg)          # a full path leaks the home dir


def test_folder_falls_back_to_the_top_level_cwd():
    mod = _load()
    assert "demo" in plain(mod.where_segment({"cwd": "/tmp/demo"}))


def test_no_folder_is_no_segment():
    assert _load().where_segment({}) is None


# --- branch ------------------------------------------------------------------


def _repo(tmp_path, head):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(head, encoding="utf-8")
    (root / "sub" / "deep").mkdir(parents=True)
    return root


def test_branch_comes_from_the_payload_when_present(tmp_path):
    mod = _load()
    seg = mod.where_segment({"workspace": {"current_dir": str(tmp_path)},
                             "worktree": {"branch": "feature/x"}})
    assert "feature/x" in plain(seg)


def test_branch_is_read_from_head_without_running_git(tmp_path, monkeypatch):
    """A statusline renders on every keystroke. A git subprocess per render is
    a noticeable lag in a big repo; reading one file is not."""
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("statusline must not shell out to git")))
    root = _repo(tmp_path, "ref: refs/heads/main\n")
    mod = _load()
    assert mod.git_branch(str(root / "sub" / "deep")) == "main"


def test_a_detached_head_shows_a_short_sha(tmp_path):
    root = _repo(tmp_path, "0123456789abcdef0123456789abcdef01234567\n")
    assert _load().git_branch(str(root)) == "0123456"


def test_a_worktree_follows_its_gitdir_pointer(tmp_path):
    """In a worktree `.git` is a file naming the real git dir, not a directory."""
    real = tmp_path / "main" / ".git" / "worktrees" / "wt"
    real.mkdir(parents=True)
    (real / "HEAD").write_text("ref: refs/heads/wt-branch\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {real}\n", encoding="utf-8")
    assert _load().git_branch(str(wt)) == "wt-branch"


def test_a_relative_gitdir_pointer_resolves_from_the_worktree(tmp_path):
    real = tmp_path / "main" / ".git" / "worktrees" / "wt"
    real.mkdir(parents=True)
    (real / "HEAD").write_text("ref: refs/heads/rel\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n", encoding="utf-8")
    assert _load().git_branch(str(wt)) == "rel"


def test_outside_a_repo_there_is_no_branch(tmp_path):
    assert _load().git_branch(str(tmp_path)) is None


def test_a_branch_name_cannot_inject_terminal_escapes(tmp_path):
    """HEAD is a file anyone with write access to the repo controls, and the
    statusline writes straight to a terminal."""
    root = _repo(tmp_path, "ref: refs/heads/evil\x1b]0;pwned\x07\n")
    b = _load().git_branch(str(root))
    assert b is None or "\x1b" not in b and "\x07" not in b


# --- cache -------------------------------------------------------------------


def test_a_warm_cache_says_nothing():
    """Silent when healthy. A segment that is always there is a segment nobody
    reads, and this one only matters when it is about to cost something."""
    mod = _load()
    assert mod.cache_segment({"prompt_cache": {"warm": True}}) is None


def test_a_cold_cache_names_what_the_next_turn_rewrites():
    mod = _load()
    seg = plain(mod.cache_segment({"prompt_cache": {"warm": False,
                                                    "recache_tokens_if_cold": 180_000}}))
    assert "cold" in seg and "180k" in seg


def test_a_recent_rebuild_is_reported_once_it_is_worth_mentioning():
    import time
    mod = _load()
    now = time.time()
    seg = plain(mod.cache_segment({"prompt_cache": {
        "warm": True, "last_miss_at": (now - 120) * 1000, "miss_recache_tokens": 90_000}},
        now=now))
    assert "rebuilt" in seg and "90k" in seg


def test_an_old_or_small_rebuild_is_not_news():
    import time
    mod = _load()
    now = time.time()
    old = {"warm": True, "last_miss_at": (now - 3600) * 1000, "miss_recache_tokens": 90_000}
    small = {"warm": True, "last_miss_at": (now - 60) * 1000, "miss_recache_tokens": 10_000}
    assert mod.cache_segment({"prompt_cache": old}, now=now) is None
    assert mod.cache_segment({"prompt_cache": small}, now=now) is None


def test_an_imminent_expiry_is_a_countdown():
    import time
    mod = _load()
    now = time.time()
    seg = plain(mod.cache_segment({"prompt_cache": {
        "warm": True, "expires_at": (now + 240) * 1000}}, now=now))
    assert "expires" in seg and "4m" in seg


def test_no_cache_block_is_no_segment():
    assert _load().cache_segment({}) is None


# --- spend limit -------------------------------------------------------------


def test_an_org_spend_limit_is_shown_once_it_matters():
    mod = _load()
    assert mod.spend_segment({"rate_limits": {"spend_limit": {"used_percentage": 40}}}) is None
    seg = plain(mod.spend_segment({"rate_limits": {"spend_limit": {"used_percentage": 76}}}))
    assert "spend 76%" in seg


def test_no_spend_limit_is_no_segment():
    assert _load().spend_segment({"rate_limits": {}}) is None


# --- band --------------------------------------------------------------------


def test_the_band_matches_the_report_by_construction():
    """The statusline cannot import tokendog, so the bands are restated there.
    This pins them to the report's, so the two can never disagree."""
    from tokendog.bands import BANDS
    assert tuple(_load().BANDS) == tuple(BANDS)


@pytest.mark.parametrize("ctx,label", [(10_000, "<50k"), (120_000, "100-150k"),
                                       (250_000, "200-400k"), (650_000, "400k+")])
def test_the_band_label(ctx, label):
    assert _load().band_of(ctx) == label


# --- cost --------------------------------------------------------------------


def test_cost_is_marked_as_an_estimate():
    """It is the client's list-price estimate, not an invoice."""
    assert plain(_load().cost_text(2.14)) == "≈$2.14"


def test_cost_drops_the_cents_once_it_is_large():
    mod = _load()
    assert plain(mod.cost_text(99.994)) == "≈$99.99"
    assert plain(mod.cost_text(99.995)) == "≈$100"
    assert plain(mod.cost_text(1234.5)) == "≈$1234"


# --- width fitting -----------------------------------------------------------


def test_an_unknown_width_reduces_nothing():
    mod = _load()
    segs = [mod.Seg("aaaa", [(1, None)]), mod.Seg("bbbb", [])]
    assert plain(mod.fit(segs, 0)) == "aaaa · bbbb"


def test_reductions_apply_least_useful_first():
    """One global order across every segment, so the least useful thing on the
    line goes first — not whatever happens to be rightmost."""
    mod = _load()
    segs = [mod.Seg("important-one", [(9, "imp")]),
            mod.Seg("trivia-segment", [(1, None)]),
            mod.Seg("medium-segment", [(5, "med")])]
    line = plain(mod.fit(segs, 40))
    assert "trivia" not in line
    assert "important-one" in line


def test_a_segment_without_reductions_never_drops():
    mod = _load()
    segs = [mod.Seg("x" * 30, []), mod.Seg("y" * 30, [(1, None)])]
    line = plain(mod.fit(segs, 20))
    assert "x" * 30 in line


def test_width_comes_from_the_environment_when_set(monkeypatch):
    monkeypatch.setenv("TOKENDOG_STATUSLINE_COLS", "88")
    assert _load().terminal_cols() == 88
    monkeypatch.delenv("TOKENDOG_STATUSLINE_COLS")
    monkeypatch.setenv("COLUMNS", "120")
    assert _load().terminal_cols() == 120


def test_a_nonsense_width_means_unknown(monkeypatch):
    monkeypatch.setenv("TOKENDOG_STATUSLINE_COLS", "wide")
    monkeypatch.setattr(os, "getppid", lambda: 1)
    assert _load().terminal_cols() >= 0


# --- the whole line ----------------------------------------------------------


def test_build_puts_where_first_and_never_throws(tmp_path):
    root = _repo(tmp_path, "ref: refs/heads/main\n")
    mod = _load()
    line = plain(mod.build({"workspace": {"current_dir": str(root)},
                            "model": {"display_name": "Opus"},
                            "context_window": {"current_usage": {"cache_read_input_tokens": 250_000},
                                               "context_window_size": 1_000_000,
                                               "used_percentage": 25},
                            "cost": {"total_cost_usd": 4.2}}))
    assert "repo ⎇ main" in line
    assert "≈$4.20" in line


def test_build_survives_garbage():
    mod = _load()
    for bad in ({"workspace": "x"}, {"prompt_cache": [1]}, {"rate_limits": None},
                {"cost": {"total_cost_usd": "lots"}}, {"worktree": 7}):
        assert isinstance(mod.build(bad), str)


# --- update notice -----------------------------------------------------------


def _plugins(tmp_path, installed, available, *, src_kind="directory"):
    """A fake ~/.claude/plugins with one install and its marketplace source."""
    home = tmp_path / "home"
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    src = tmp_path / "src"
    (src / "tokendog-plugin" / ".claude-plugin").mkdir(parents=True)
    (src / ".claude-plugin").mkdir(parents=True)
    (src / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"name": "tokendog", "plugins": [{"name": "tokendog", "source": "./tokendog-plugin",
                                          "version": available}]}))
    (src / "tokendog-plugin" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "tokendog", "version": available}))
    (plugins / "installed_plugins.json").write_text(json.dumps({"plugins": {
        "tokendog@tokendog": [{"version": installed, "lastUpdated": "2026-09-29T00:00:00Z"}]}}))
    (plugins / "known_marketplaces.json").write_text(json.dumps({"tokendog": {
        "source": {"source": src_kind, "path": str(src)}, "installLocation": str(src)}}))
    return home


import json  # noqa: E402


def test_a_plugin_behind_its_source_says_so(tmp_path):
    mod = _load()
    home = _plugins(tmp_path, "0.5.0", "0.6.0")
    seg = plain(mod.update_segment(home=home, use_cache=False))
    assert "0.5.0→0.6.0" in seg and "/plugin update" in seg


def test_an_up_to_date_plugin_says_nothing(tmp_path):
    mod = _load()
    home = _plugins(tmp_path, "0.6.0", "0.6.0")
    assert mod.update_segment(home=home, use_cache=False) is None


def test_a_newer_install_than_source_is_not_an_update(tmp_path):
    mod = _load()
    home = _plugins(tmp_path, "0.7.0", "0.6.0")
    assert mod.update_segment(home=home, use_cache=False) is None


def test_versions_are_only_compared_like_with_like(tmp_path):
    """The claude.ai org directory versions as "0017". Comparing that with a
    semver gives a confident wrong answer; no notice is better."""
    mod = _load()
    home = _plugins(tmp_path, "0017", "0.6.0")
    assert mod.update_segment(home=home, use_cache=False) is None
    assert mod.version_newer("0.6.0", "0.10.0") is True     # numeric, not string order
    assert mod.version_newer("0017", "0018") is True
    assert mod.version_newer("0017", "1.0.0") is None


def test_a_version_cannot_inject_terminal_escapes(tmp_path):
    """One of the two versions is read from a file a marketplace controls."""
    mod = _load()
    home = _plugins(tmp_path, "0.5.0", "0.6.0\x1b]0;pwned\x07")
    seg = mod.update_segment(home=home, use_cache=False)
    assert seg is None or ("\x1b]" not in seg and "\x07" not in seg)


def test_no_install_record_is_no_notice(tmp_path):
    mod = _load()
    assert mod.update_segment(home=tmp_path / "nobody", use_cache=False) is None


def test_the_lookup_is_cached_so_it_does_not_run_every_keystroke(tmp_path, monkeypatch):
    mod = _load()
    home = _plugins(tmp_path, "0.5.0", "0.6.0")
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "state"))
    first = mod.update_segment(home=home)
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text("{}")
    assert mod.update_segment(home=home) == first      # served from the cache


# --- restart notice ----------------------------------------------------------


def _updated(tmp_path, installed, available, *, updated_ago_s):
    from datetime import datetime, timedelta, timezone
    home = _plugins(tmp_path, installed, available)
    when = (datetime.now(timezone.utc) - timedelta(seconds=updated_ago_s)).isoformat()
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    d = json.loads(p.read_text())
    d["plugins"]["tokendog@tokendog"][0]["lastUpdated"] = when.replace("+00:00", "Z")
    p.write_text(json.dumps(d))
    return home


def test_a_session_older_than_the_install_is_told_to_restart(tmp_path):
    """The installed copy is current, but this window loaded its hooks at
    startup, before the update. Nothing else on screen says so."""
    mod = _load()
    home = _updated(tmp_path, "0.10.2", "0.10.2", updated_ago_s=300)
    seg = plain(mod.update_segment(home=home, use_cache=False,
                                   session_age_s=3_600))
    assert seg == "0.10.2 installed, /reload-plugins to load it"


def test_a_session_started_after_the_install_is_current(tmp_path):
    mod = _load()
    home = _updated(tmp_path, "0.10.2", "0.10.2", updated_ago_s=3_600)
    assert mod.update_segment(home=home, use_cache=False, session_age_s=300) is None


def test_being_behind_the_source_still_wins(tmp_path):
    mod = _load()
    home = _updated(tmp_path, "0.10.1", "0.10.2", updated_ago_s=300)
    seg = plain(mod.update_segment(home=home, use_cache=False, session_age_s=3_600))
    assert seg == "v0.10.1→0.10.2 /plugin update"


def test_no_session_age_means_no_restart_verdict(tmp_path):
    mod = _load()
    home = _updated(tmp_path, "0.10.2", "0.10.2", updated_ago_s=300)
    assert mod.update_segment(home=home, use_cache=False, session_age_s=None) is None


def test_build_passes_the_session_age_through(tmp_path, monkeypatch):
    mod = _load()
    home = _updated(tmp_path, "0.10.2", "0.10.2", updated_ago_s=300)
    monkeypatch.setattr(mod.os.path, "expanduser",
                        lambda p: str(home) if p == "~" else p.replace("~", str(home), 1))
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / "state2"))
    line = plain(mod.build({"cost": {"total_duration_ms": 3_600_000}}))
    assert "/reload-plugins to load it" in line


# --- a /reload-plugins after the install means it is loaded --------------------


def _reload_transcript(tmp_path, *, reload_ago_s, quote_only=False):
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(seconds=reload_ago_s)).isoformat().replace("+00:00", "Z")
    recs = [{"type": "user", "timestamp": ts, "message": {"content": "hello"}}]
    if quote_only:
        # Someone MENTIONING the command is not the command running.
        recs.append({"type": "assistant", "timestamp": ts, "message": {"content": [
            {"type": "text", "text": "run /reload-plugins; it prints <local-command-stdout>Reloaded: 8 plugins"}]}})
    else:
        recs.append({"type": "system", "subtype": "local_command", "timestamp": ts,
                     "content": "<local-command-stdout>Reloaded: 8 plugins · 17 hooks</local-command-stdout>"})
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return str(p)


def test_a_reload_after_the_install_clears_the_restart_notice(tmp_path):
    """/reload-plugins loads the update without restarting, so the session's
    start time never moves. The reload itself is the evidence."""
    mod = _load()
    home = _updated(tmp_path, "0.10.3", "0.10.3", updated_ago_s=300)
    t = _reload_transcript(tmp_path, reload_ago_s=60)
    assert mod.update_segment(home=home, use_cache=False, session_age_s=3_600,
                              transcript=t) is None


def test_a_reload_before_the_install_does_not_count(tmp_path):
    mod = _load()
    home = _updated(tmp_path, "0.10.3", "0.10.3", updated_ago_s=300)
    t = _reload_transcript(tmp_path, reload_ago_s=900)
    seg = plain(mod.update_segment(home=home, use_cache=False, session_age_s=3_600, transcript=t))
    assert "/reload-plugins to load it" in seg


def test_mentioning_the_command_is_not_running_it(tmp_path):
    mod = _load()
    home = _updated(tmp_path, "0.10.3", "0.10.3", updated_ago_s=300)
    t = _reload_transcript(tmp_path, reload_ago_s=60, quote_only=True)
    seg = plain(mod.update_segment(home=home, use_cache=False, session_age_s=3_600, transcript=t))
    assert "/reload-plugins to load it" in seg


def test_the_notice_now_says_reload_is_enough(tmp_path):
    mod = _load()
    home = _updated(tmp_path, "0.10.3", "0.10.3", updated_ago_s=300)
    seg = plain(mod.update_segment(home=home, use_cache=False, session_age_s=3_600))
    assert "/reload-plugins" in seg


# --- an update that changes nothing a session loads needs no reload ------------


def _cached(tmp_path, version, *, hooks='{"hooks": {}}', script="print(1)"):
    root = tmp_path / "cache" / "tokendog" / "tokendog" / version
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "tokendog", "version": version}))
    (root / "hooks").mkdir()
    (root / "hooks" / "hooks.json").write_text(hooks)
    (root / "scripts").mkdir()
    (root / "scripts" / "statusline.py").write_text(script)
    return root


def _with_install_path(home, path):
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    d = json.loads(p.read_text())
    d["plugins"]["tokendog@tokendog"][0]["installPath"] = str(path)
    p.write_text(json.dumps(d))


def test_a_script_only_update_needs_no_reload(tmp_path):
    """Hook scripts and the statusline are looked up per call: an update that
    only changes them is already live in every open window."""
    mod = _load()
    _cached(tmp_path, "0.10.3", script="old")
    new = _cached(tmp_path, "0.10.4", script="new")
    home = _updated(tmp_path, "0.10.4", "0.10.4", updated_ago_s=300)
    _with_install_path(home, new)
    assert mod.update_segment(home=home, use_cache=False, session_age_s=3_600) is None


def test_an_update_that_changes_hooks_still_asks_for_a_reload(tmp_path):
    mod = _load()
    _cached(tmp_path, "0.10.3", hooks='{"hooks": {}}')
    new = _cached(tmp_path, "0.10.4", hooks='{"hooks": {"Stop": []}}')
    home = _updated(tmp_path, "0.10.4", "0.10.4", updated_ago_s=300)
    _with_install_path(home, new)
    seg = plain(mod.update_segment(home=home, use_cache=False, session_age_s=3_600))
    assert seg == "0.10.4 installed, /reload-plugins to load it"


def test_no_previous_version_to_compare_keeps_the_notice(tmp_path):
    mod = _load()
    new = _cached(tmp_path, "0.10.4")
    home = _updated(tmp_path, "0.10.4", "0.10.4", updated_ago_s=300)
    _with_install_path(home, new)
    assert "/reload-plugins" in plain(mod.update_segment(home=home, use_cache=False,
                                                         session_age_s=3_600))


def test_the_live_install_compares_against_the_highest_older_version(tmp_path):
    mod = _load()
    _cached(tmp_path, "0.9.0", hooks="ancient")          # would differ, but is not the neighbour
    _cached(tmp_path, "0.10.3")
    new = _cached(tmp_path, "0.10.4", script="changed")
    assert mod.needs_reload(str(new)) is False
