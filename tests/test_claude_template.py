from pathlib import Path
from tokendog.templates import MARKER
TPL = Path(__file__).resolve().parents[1] / "tokendog-templates" / "CLAUDE.md.template"

def test_template_has_marker_and_frugal_rules():
    txt = TPL.read_text()
    assert MARKER in txt
    low = txt.lower()
    for kw in ("offset", "grep", "bash", "brevity"):
        assert kw in low, f"missing: {kw}"
    # marker is at the end — nothing frugal below it
    assert txt.strip().endswith(MARKER) or txt.rstrip().endswith(MARKER)
