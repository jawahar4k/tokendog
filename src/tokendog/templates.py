from __future__ import annotations
import json
from pathlib import Path

MARKER = "<!-- TOKENDOG_EXTENSION_MARKER -->"


def repo_templates_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tokendog-templates"


def statusline_script() -> Path:
    """Absolute path to the statusline script inside this checkout."""
    return (Path(__file__).resolve().parents[2]
            / "tokendog-plugin" / "scripts" / "statusline.py")


def render_settings(template_text: str, statusline: Path | str | None = None) -> str:
    """Add a `statusLine` entry pointing at this checkout's statusline script.

    The path is resolved at install time rather than written into the template,
    because a static template cannot know where the repo lives and the
    statusline is invoked by absolute path — `${CLAUDE_PLUGIN_ROOT}` is expanded
    for hooks, not here.

    A settings file that already names a statusLine is left alone: the reader
    chose that one, and silently replacing it would be the kind of surprise this
    tool exists to avoid.
    """
    data = json.loads(template_text)
    if "statusLine" not in data:
        script = Path(statusline) if statusline else statusline_script()
        data["statusLine"] = {"type": "command", "command": f"python3 {script}"}
    return json.dumps(data, indent=2) + "\n"


def _org_section(existing: str) -> str:
    idx = existing.find(MARKER)
    return "" if idx == -1 else existing[idx + len(MARKER):]


def render_claude_md(template_text: str, org_section: str = "") -> str:
    base = template_text if MARKER in template_text else template_text.rstrip() + "\n\n" + MARKER + "\n"
    if org_section.strip():
        base = base.rstrip() + "\n" + (org_section[1:] if org_section.startswith("\n") else org_section)
    return base if base.endswith("\n") else base + "\n"


def apply_templates(templates_dir, target_dir, force: bool = False) -> list[Path]:
    templates_dir = Path(templates_dir)
    target_dir = Path(target_dir)
    written: list[Path] = []

    claude_tpl = (templates_dir / "CLAUDE.md.template").read_text(encoding="utf-8")
    target_claude = target_dir / "CLAUDE.md"
    org = _org_section(target_claude.read_text(encoding="utf-8")) if target_claude.exists() else ""
    target_claude.parent.mkdir(parents=True, exist_ok=True)
    target_claude.write_text(render_claude_md(claude_tpl, org), encoding="utf-8")
    written.append(target_claude)

    settings_tpl = (templates_dir / "settings.json.template").read_text(encoding="utf-8")
    settings = target_dir / ".claude" / "settings.json"
    if force or not settings.exists():
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(render_settings(settings_tpl), encoding="utf-8")
        written.append(settings)
    return written
