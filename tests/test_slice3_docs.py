# tests/test_slice3_docs.py
from pathlib import Path
from tokendog.templates import MARKER
T = Path(__file__).resolve().parents[1] / "tokendog-templates"

def test_mcp_hygiene_present():
    txt = (T / "mcp-hygiene.md").read_text().lower()
    assert "mcp" in txt and "disable" in txt

def test_example_extension_uses_marker():
    ex = (T / "example-extension" / "CLAUDE.md.example").read_text()
    assert MARKER in ex
    # org content appears BELOW the marker
    assert ex.split(MARKER, 1)[1].strip() != ""
    assert (T / "example-extension" / "README.md").exists()
