from pathlib import Path
D = Path(__file__).resolve().parents[1] / "tokendog-mcp-toolkit" / "docs"

def test_docs_exist():
    for name in ("deferred-loading", "pagination", "batch-queries", "structured-schemas"):
        p = D / f"{name}.md"
        assert p.exists() and len(p.read_text()) > 100, name
