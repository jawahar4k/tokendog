"""`tokendog init` wires the statusline, and never overwrites one you chose."""
import json
from pathlib import Path

from tokendog.templates import (
    apply_templates,
    render_settings,
    repo_templates_dir,
    statusline_script,
)


def test_the_statusline_script_exists_where_init_points():
    assert statusline_script().is_file()


def test_render_adds_a_statusline_with_an_absolute_path():
    out = json.loads(render_settings('{"model": "x"}'))
    cmd = out["statusLine"]["command"]
    assert out["statusLine"]["type"] == "command"
    assert cmd.startswith("python3 /")
    assert cmd.endswith("statusline.py")


def test_render_preserves_everything_else_in_the_template():
    tpl = '{"model": "m", "env": {"A": "1"}, "permissions": {"allow": ["Read"]}}'
    out = json.loads(render_settings(tpl))
    assert out["model"] == "m"
    assert out["env"] == {"A": "1"}
    assert out["permissions"] == {"allow": ["Read"]}


def test_an_existing_statusline_is_left_alone():
    """The reader chose that one; replacing it silently would be a surprise."""
    tpl = json.dumps({"statusLine": {"type": "command", "command": "mine.sh"}})
    out = json.loads(render_settings(tpl))
    assert out["statusLine"]["command"] == "mine.sh"


def test_the_path_is_injectable_for_testing():
    out = json.loads(render_settings("{}", statusline="/tmp/sl.py"))
    assert out["statusLine"]["command"] == "python3 /tmp/sl.py"


def test_init_writes_a_settings_file_carrying_the_statusline(tmp_path):
    written = apply_templates(repo_templates_dir(), tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    assert settings in written
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert "statusLine" in data
    assert Path(data["statusLine"]["command"].split(" ", 1)[1]).is_file()


def test_init_still_writes_valid_json(tmp_path):
    apply_templates(repo_templates_dir(), tmp_path)
    json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))


def test_init_does_not_clobber_an_existing_settings_file(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"mine": true}', encoding="utf-8")
    apply_templates(repo_templates_dir(), tmp_path)
    assert json.loads(settings.read_text(encoding="utf-8")) == {"mine": True}
