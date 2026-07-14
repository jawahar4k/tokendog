from pathlib import Path
D = Path(__file__).resolve().parents[1] / "tokendog-docs"

def test_mkdocs_config_references_pages():
    cfg = (D / "mkdocs.yml").read_text()
    assert "site_name" in cfg and "TokenDog" in cfg
    for page in ("quickstart.md", "how-it-works.md", "comparison.md", "faq.md", "extending.md"):
        assert page in cfg, f"nav missing {page}"

def test_quickstart_has_install_and_cost_command():
    q = (D / "quickstart.md").read_text().lower()
    assert "install" in q and "/tokendog:cost" in q
