from pathlib import Path
from tokendog import report


def test_init_writes_templates(tmp_path, monkeypatch, capsys):
    # point the module at a fixture templates dir
    tdir = tmp_path / "tokendog-templates"; tdir.mkdir()
    from tokendog.templates import MARKER
    (tdir / "CLAUDE.md.template").write_text("# frugal\n- offset\n\n" + MARKER + "\n")
    (tdir / "settings.json.template").write_text('{"model": "claude-sonnet-4-6"}\n')
    monkeypatch.setattr("tokendog.report.repo_templates_dir", lambda: tdir)
    target = tmp_path / "repo"
    rc = report.main(["init", "--target", str(target)])
    assert rc == 0
    assert (target / "CLAUDE.md").exists()
    assert (target / ".claude" / "settings.json").exists()
    assert "CLAUDE.md" in capsys.readouterr().out
