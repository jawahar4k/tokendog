from pathlib import Path
SKILL = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "skills" / "tokendog-frugal" / "SKILL.md"

def test_frontmatter_and_guidance():
    txt = SKILL.read_text()
    assert txt.startswith("---")
    assert "name:" in txt and "description:" in txt
    low = txt.lower()
    for kw in ("offset", "grep", "bash", "brevity", "structured"):
        assert kw in low, f"missing frugal guidance: {kw}"
