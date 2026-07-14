from pathlib import Path

CMD = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "commands"


def test_budget_command_present():
    t = (CMD / "tokendog-budget.md").read_text()
    assert t.startswith("---") and "tokendog.report" in t and "budget" in t.lower()


def test_audit_command_present():
    t = (CMD / "tokendog-audit.md").read_text()
    assert t.startswith("---") and "audit" in t.lower()
