"""Writing into someone else's settings.json: ownership, consent, reversal."""
import json

import pytest

from tokendog.settings_install import (
    MARKER,
    apply,
    plan,
    status,
    uninstall,
)


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude").mkdir(parents=True)
    return tmp_path


def _settings(home) -> dict:
    return json.loads((home / ".claude" / "settings.json").read_text())


def _write(home, data):
    (home / ".claude" / "settings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


# --- ownership ---------------------------------------------------------------


def test_a_key_we_wrote_carries_a_marker_that_says_so(home):
    apply(home=home)
    assert MARKER in json.dumps(_settings(home)["statusLine"])
    assert status(home=home)["statusLine"] == "installed"


def test_ownership_is_exact_not_a_substring(home):
    """A user's own `~/my-tokendog-statusline.sh` contains our name. Matching on
    a substring would adopt it, overwrite it, then delete it on uninstall."""
    _write(home, {"statusLine": {"type": "command",
                                 "command": "bash ~/my-tokendog-statusline.sh"}})
    assert status(home=home)["statusLine"] == "other"
    assert plan(home=home)["statusLine"]["action"] == "blocked"


def test_whitespace_does_not_change_who_owns_a_key(home):
    apply(home=home)
    s = _settings(home)
    s["statusLine"]["command"] = "  " + s["statusLine"]["command"].replace(" ", "  ") + " "
    _write(home, s)
    assert status(home=home)["statusLine"] == "installed"


# --- consent -----------------------------------------------------------------


def test_someone_elses_statusline_is_never_overwritten(home):
    _write(home, {"statusLine": {"type": "command", "command": "starship prompt"}})
    out = apply(home=home)
    assert out["applied"] is False
    assert out["blocked"] == ["statusLine"]
    assert _settings(home)["statusLine"]["command"] == "starship prompt"


def test_replace_is_the_explicit_yes(home):
    _write(home, {"statusLine": {"type": "command", "command": "starship prompt"}})
    out = apply(home=home, replace=True)
    assert out["applied"] is True
    assert MARKER in json.dumps(_settings(home)["statusLine"])


def test_what_was_replaced_is_kept_so_it_can_come_back(home):
    _write(home, {"statusLine": {"type": "command", "command": "starship prompt"}})
    apply(home=home, replace=True)
    uninstall(home=home)
    assert _settings(home)["statusLine"] == {"type": "command", "command": "starship prompt"}


def test_uninstall_removes_a_key_that_had_nothing_before_it(home):
    apply(home=home)
    uninstall(home=home)
    assert "statusLine" not in _settings(home)


def test_uninstall_leaves_a_key_we_do_not_own(home):
    _write(home, {"statusLine": {"type": "command", "command": "starship prompt"}})
    uninstall(home=home)
    assert _settings(home)["statusLine"]["command"] == "starship prompt"


# --- safety ------------------------------------------------------------------


def test_invalid_json_is_refused_not_rewritten(home):
    """Someone's settings file with a trailing comma is still their settings
    file. Parsing it loosely and writing it back reformatted loses their
    comments and their intent."""
    (home / ".claude" / "settings.json").write_text('{"statusLine": ,}', encoding="utf-8")
    out = apply(home=home)
    assert out["applied"] is False and "invalid" in out["error"].lower()
    assert (home / ".claude" / "settings.json").read_text() == '{"statusLine": ,}'


def test_a_backup_is_written_before_any_change(home):
    _write(home, {"model": "opus"})
    apply(home=home)
    backups = list((home / ".claude").glob("settings.json.bak-tokendog*"))
    assert backups and json.loads(backups[0].read_text()) == {"model": "opus"}


def test_nothing_else_in_the_file_is_disturbed(home):
    _write(home, {"model": "opus", "env": {"A": "1"}, "permissions": {"allow": ["Bash"]}})
    apply(home=home)
    s = _settings(home)
    assert s["model"] == "opus" and s["env"] == {"A": "1"}
    assert s["permissions"] == {"allow": ["Bash"]}


def test_a_missing_settings_file_is_created(home):
    apply(home=home)
    assert (home / ".claude" / "settings.json").is_file()


def test_no_partial_file_is_ever_left_behind(home):
    apply(home=home)
    assert not list((home / ".claude").glob("*.tmp*"))


# --- dry run -----------------------------------------------------------------


def test_plan_changes_nothing(home):
    _write(home, {"model": "opus"})
    before = (home / ".claude" / "settings.json").read_text()
    p = plan(home=home)
    assert p["statusLine"]["action"] == "install"
    assert (home / ".claude" / "settings.json").read_text() == before


def test_plan_shows_the_diff_it_would_apply(home):
    p = plan(home=home)
    assert p["statusLine"]["to"]["type"] == "command"
    assert p["statusLine"]["from"] is None


def test_plan_reports_no_change_when_already_installed(home):
    apply(home=home)
    assert plan(home=home)["statusLine"]["action"] == "unchanged"


def test_plan_reports_drift_when_the_command_points_somewhere_else(home):
    """Installed from another checkout, which still exists. Ours, runnable,
    and not what this one would write."""
    other = home / "other-checkout" / "statusline.py"
    other.parent.mkdir(parents=True); other.write_text("#", encoding="utf-8")
    apply(home=home)
    s = _settings(home)
    s["statusLine"]["command"] = f"python3 {other}  # {MARKER}"
    _write(home, s)
    p = plan(home=home)
    assert p["statusLine"]["action"] == "update"
    assert status(home=home)["statusLine"] == "drifted"


# --- status ------------------------------------------------------------------


def test_status_on_an_untouched_machine(home):
    assert status(home=home)["statusLine"] == "none"


def test_status_says_broken_when_the_script_is_gone(home):
    """A folder rename orphans the command. It is still ours and still parses,
    and it prints nothing — so "drifted" would send the reader hunting for the
    wrong problem."""
    apply(home=home)
    s = _settings(home)
    s["statusLine"]["command"] = f"python3 /gone/statusline.py  # {MARKER}"
    _write(home, s)
    assert status(home=home)["statusLine"] == "broken"


# --- the command surface -----------------------------------------------------


def test_format_names_the_action_and_the_way_out(home):
    from tokendog.report import format_settings_plan
    _write(home, {"statusLine": {"type": "command", "command": "starship prompt"}})
    out = format_settings_plan(plan(home=home), status(home=home))
    assert "statusLine" in out
    assert "starship prompt" in out              # says what is in the way
    assert "--replace" in out                    # and how to proceed anyway


def test_format_reports_a_clean_install(home):
    from tokendog.report import format_settings_plan
    out = format_settings_plan(plan(home=home), status(home=home))
    assert "will add" in out and "would be" in out


def test_format_refuses_rather_than_guessing_on_invalid_json(home):
    from tokendog.report import format_settings_plan
    (home / ".claude" / "settings.json").write_text("{,}", encoding="utf-8")
    out = format_settings_plan(plan(home=home), status(home=home))
    assert "invalid" in out.lower() and "untouched" in out.lower()


def test_a_statusline_written_by_an_older_init_is_recognised_as_ours(home):
    """`tokendog init` wrote this key for a year with no marker. Treating every
    one of those as a stranger's would block the upgrade for everyone who has
    the tool installed."""
    from tokendog.templates import statusline_script
    _write(home, {"statusLine": {"type": "command",
                                 "command": f"python3 {statusline_script()}"}})
    assert status(home=home)["statusLine"] == "drifted"
    assert plan(home=home)["statusLine"]["action"] == "update"
    apply(home=home)
    assert MARKER in json.dumps(_settings(home)["statusLine"])


def test_adoption_is_by_resolved_path_not_by_name(home):
    """The rule that makes the above safe: it is ours when the command runs the
    file we ship, not when the command happens to mention us."""
    _write(home, {"statusLine": {"type": "command",
                                 "command": "python3 /home/me/tokendog-statusline.py"}})
    assert status(home=home)["statusLine"] == "other"
