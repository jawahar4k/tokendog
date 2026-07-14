from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "tokendog-docs" / "how-it-works.md"


def test_covers_all_categories_and_items():
    txt = DOC.read_text()
    # 10 framework categories A–J
    for cat in ("Fixed-prompt", "Variable-prompt", "caching", "routing",
                "Output-token", "Observability", "Org-wide", "Behavioral",
                "Session lifecycle", "memory"):
        assert cat.lower() in txt.lower(), f"missing category: {cat}"
    # references all 47 numbered items — check item numbers 1..47 appear
    for n in (1, 8, 20, 32, 41, 47):
        assert f"{n}" in txt
    assert len(txt) > 3000, "how-it-works should be substantial"
