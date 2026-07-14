import json
from pathlib import Path
TPL = Path(__file__).resolve().parents[1] / "tokendog-templates" / "settings.json.template"

def test_valid_json_with_expected_keys():
    data = json.loads(TPL.read_text())
    assert isinstance(data, dict)
    assert "model" in data
    assert "cleanupPeriodDays" in data       # session lifecycle / compaction tuning (item 46)
    assert "permissions" in data
