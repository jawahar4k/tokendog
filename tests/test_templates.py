from pathlib import Path
from tokendog import templates


def _make_templates(dir: Path):
    dir.mkdir(parents=True, exist_ok=True)
    (dir / "CLAUDE.md.template").write_text(
        "# TokenDog frugality defaults\n- prefer Read offset+limit\n\n" + templates.MARKER + "\n")
    (dir / "settings.json.template").write_text('{"model": "claude-sonnet-4-6"}\n')


def test_apply_writes_both(tmp_path):
    tdir = tmp_path / "templates"; _make_templates(tdir)
    target = tmp_path / "repo"
    written = templates.apply_templates(tdir, target)
    claude = (target / "CLAUDE.md").read_text()
    assert templates.MARKER in claude and "offset+limit" in claude
    assert (target / ".claude" / "settings.json").exists()
    assert len(written) == 2


def test_reapply_preserves_org_section(tmp_path):
    tdir = tmp_path / "templates"; _make_templates(tdir)
    target = tmp_path / "repo"
    templates.apply_templates(tdir, target)
    claude = target / "CLAUDE.md"
    claude.write_text(claude.read_text().rstrip() + "\n# Org: internal services at foo/bar\n")
    # change the template's top section, re-apply
    (tdir / "CLAUDE.md.template").write_text(
        "# TokenDog frugality defaults v2\n- cap Bash at 200 lines\n\n" + templates.MARKER + "\n")
    templates.apply_templates(tdir, target)
    out = claude.read_text()
    assert "v2" in out and "cap Bash" in out      # top refreshed
    assert "internal services at foo/bar" in out  # org section preserved


def test_reapply_preserves_blank_line_after_marker(tmp_path):
    tdir = tmp_path / "templates"; _make_templates(tdir)
    target = tmp_path / "repo"
    templates.apply_templates(tdir, target)
    claude = target / "CLAUDE.md"
    # Simulate user adding a blank line between MARKER and their org content
    claude.write_text(claude.read_text().rstrip() + "\n\n# Org: internal services\n")
    templates.apply_templates(tdir, target)
    out = claude.read_text()
    assert "internal services" in out
    # The blank line must survive — MARKER + \n + \n + content
    marker_pos = out.find(templates.MARKER)
    after_marker = out[marker_pos + len(templates.MARKER):]
    assert after_marker.startswith("\n\n"), "blank line after MARKER was stripped"


def test_settings_not_clobbered_without_force(tmp_path):
    tdir = tmp_path / "templates"; _make_templates(tdir)
    target = tmp_path / "repo"
    templates.apply_templates(tdir, target)
    s = target / ".claude" / "settings.json"
    s.write_text('{"model": "custom"}')
    templates.apply_templates(tdir, target)               # no force
    assert '"custom"' in s.read_text()
    templates.apply_templates(tdir, target, force=True)   # force overwrites
    assert "claude-sonnet-4-6" in s.read_text()
