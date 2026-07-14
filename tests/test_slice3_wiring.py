import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / "tokendog-plugin"

def test_init_command_registered():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert "./commands/tokendog-init.md" in m["commands"]
    # earlier commands still present
    assert "./commands/tokendog-cost.md" in m["commands"]
    assert "./commands/tokendog-budget.md" in m["commands"]
