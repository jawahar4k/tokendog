"""Turning a connector off is the only write tokendog makes. It is guarded."""
import json

import pytest

from tokendog import disable


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text(json.dumps({
        "mcpServers": {"gone": {"command": "node", "args": ["a.js"]},
                       "keep": {"command": "k"}},
        "projects": {"/x/alpha": {"mcpServers": {"proj": {"command": "p"}}}},
    }), encoding="utf-8")
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"ctx@market": True}}), encoding="utf-8")
    d = tmp_path / ".claude" / "plugins" / "cache" / "market" / "ctx" / "1.0"
    d.mkdir(parents=True)
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {"ctx": {"url": "http://x"}}}),
                                 encoding="utf-8")
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path / ".tokendog"))
    return tmp_path


def servers(home):
    return json.load(open(home / ".claude.json"))["mcpServers"]


# --- planning is pure --------------------------------------------------


def test_a_plan_changes_nothing(home):
    before = json.load(open(home / ".claude.json"))
    p = disable.plan("gone", home=home)
    assert p["ok"] and p["scope"] == "global"
    assert json.load(open(home / ".claude.json")) == before


def test_an_unknown_connector_cannot_be_disabled(home):
    p = disable.plan("nosuch", home=home)
    assert p["ok"] is False
    assert "nothing to disable" in p["reason"]


def test_the_three_scopes_each_name_their_own_edit(home):
    assert "mcpServers['gone']" in disable.plan("gone", home=home)["edits"][0]["change"]
    assert "disabledMcpjsonServers" in disable.plan("proj", home=home)["edits"][0]["change"]
    assert "enabledPlugins" in disable.plan("plugin_ctx_ctx", home=home)["edits"][0]["change"]


def test_a_plan_carries_projects_structurally_not_as_prose(home):
    """apply() reads these fields; parsing them back out of display text was a bug."""
    p = disable.plan("proj", home=home)
    assert p["projects"] == ["alpha"]


# --- applying is reversible -------------------------------------------


def test_disabling_a_global_server_removes_only_that_one(home):
    disable.apply("gone", home=home)
    assert "gone" not in servers(home)
    assert "keep" in servers(home)


def test_the_file_is_backed_up_before_it_is_touched(home):
    r = disable.apply("gone", home=home)
    assert r["backups"]
    from pathlib import Path
    assert Path(r["backups"][0]).is_file()
    assert "gone" in json.load(open(r["backups"][0]))["mcpServers"], (
        "the backup must be the state BEFORE the edit")


def test_the_removed_config_is_stashed_verbatim(home):
    disable.apply("gone", home=home)
    stash = json.load(open(home / ".tokendog" / "disabled.json"))
    assert stash["gone"]["config"] == {"command": "node", "args": ["a.js"]}


def test_re_enabling_restores_the_config_exactly(home):
    original = servers(home)["gone"]
    disable.apply("gone", home=home)
    disable.apply("gone", home=home, enable=True)
    assert servers(home)["gone"] == original


def test_a_project_server_is_disabled_by_listing_it(home):
    disable.apply("proj", home=home)
    projects = json.load(open(home / ".claude.json"))["projects"]
    assert "proj" in projects["/x/alpha"]["disabledMcpjsonServers"]
    assert "proj" in projects["/x/alpha"]["mcpServers"], "config is kept, not deleted"


def test_re_enabling_a_project_server_delists_it(home):
    disable.apply("proj", home=home)
    disable.apply("proj", home=home, enable=True)
    projects = json.load(open(home / ".claude.json"))["projects"]
    assert projects["/x/alpha"]["disabledMcpjsonServers"] == []


def test_a_plugin_connector_is_switched_off_in_settings(home):
    disable.apply("plugin_ctx_ctx", home=home)
    s = json.load(open(home / ".claude" / "settings.json"))
    assert s["enabledPlugins"]["ctx@market"] is False


def test_disabling_twice_is_refused_not_repeated(home):
    disable.apply("gone", home=home)
    p = disable.plan("gone", home=home)
    assert p["ok"] is False


def test_enabling_something_already_on_is_refused(home):
    assert disable.plan("keep", home=home, enable=True)["ok"] is False


def test_restoring_with_nothing_stashed_fails_loudly(home):
    """Better to refuse than to invent a config."""
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    r = disable.apply("gone", home=home, enable=True)
    assert r["ok"] is False


def test_the_written_file_is_valid_json(home):
    disable.apply("gone", home=home)
    json.load(open(home / ".claude.json"))


def test_no_temp_file_is_left_behind(home):
    disable.apply("gone", home=home)
    assert not (home / ".claude.json.tmp").exists()
