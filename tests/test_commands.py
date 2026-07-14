from pathlib import Path

CMD = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "commands"


def test_cost_command_frontmatter_and_body():
    txt = (CMD / "tokendog-cost.md").read_text()
    assert txt.startswith("---")
    assert "description:" in txt
    assert "tokendog.report" in txt  # injects real rollup


def test_doctor_command_present():
    txt = (CMD / "tokendog-doctor.md").read_text()
    assert "doctor" in txt.lower()
