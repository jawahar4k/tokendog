import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / "tokendog-plugin"

def test_hooks_wired():
    h = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    pre = json.dumps(h["hooks"]["PreToolUse"])
    post = json.dumps(h["hooks"]["PostToolUse"])
    stop = json.dumps(h["hooks"]["Stop"])
    assert "budget_enforce.py" in pre
    assert "truncate_output.py" in post
    assert "session_summary.py" in stop and "budget_alert.py" in stop
    assert "token_count.py" in post  # Slice 1 hook still present

def test_manifest_registers_skills_and_commands():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    cmds = json.dumps(m.get("commands", []))
    assert "tokendog-budget.md" in cmds and "tokendog-audit.md" in cmds
    # skills auto-discovered from skills/ dir; assert the dirs exist
    assert (ROOT / "skills" / "tokendog-frugal" / "SKILL.md").exists()
    assert (ROOT / "skills" / "tokendog-hygiene" / "SKILL.md").exists()
