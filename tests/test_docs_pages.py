# tests/test_docs_pages.py
from pathlib import Path
D = Path(__file__).resolve().parents[1] / "docs"

def test_extending_covers_marker_and_backends():
    txt = (D / "extending.md").read_text()
    assert "TOKENDOG_EXTENSION_MARKER" in txt
    assert "backend" in txt.lower()

def test_comparison_is_fair_to_headroom():
    """The page once called TokenDog "a superset of Headroom". It is not, and the
    comparison must say what Headroom does that TokenDog does not."""
    txt = (D / "comparison.md").read_text().lower()
    assert "headroom" in txt and "proxy" in txt and "reversible" in txt
    assert "superset" not in txt

def test_faq_nonempty():
    assert len((D / "faq.md").read_text()) > 200
