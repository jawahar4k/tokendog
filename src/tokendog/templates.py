from __future__ import annotations
from pathlib import Path

MARKER = "<!-- TOKENDOG_EXTENSION_MARKER -->"


def repo_templates_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tokendog-templates"


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
        settings.write_text(settings_tpl, encoding="utf-8")
        written.append(settings)
    return written
