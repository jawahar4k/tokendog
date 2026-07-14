from pathlib import Path

CMD = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "commands" / "tokendog-init.md"


def test_init_command_present():
    txt = CMD.read_text()
    assert txt.startswith("---") and "tokendog.report init" in txt
