from pathlib import Path
SKILL = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "skills" / "tokendog-hygiene" / "SKILL.md"

def test_hygiene_content():
    txt = SKILL.read_text()
    assert txt.startswith("---") and "description:" in txt
    low = txt.lower()
    for kw in ("clear", "batch", "paste"):
        assert kw in low, f"missing hygiene guidance: {kw}"
