import json, importlib.util, sys, types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "tokendog-plugin"

def test_plugin_manifest_valid():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert m["name"] == "tokendog"
    assert "tokendog-cost" in m["mcpServers"]


def test_manifest_does_not_declare_the_standard_hooks_file():
    """The manifest must NOT name ./hooks/hooks.json.

    Claude Code loads the standard `hooks/hooks.json` automatically. A manifest
    that also declares it is REJECTED at install time with "Duplicate hooks file
    detected: ./hooks/hooks.json resolves to already-loaded file ...", and the
    whole plugin fails to load — hooks, skills and commands together.

    This test previously asserted the opposite (`m["hooks"] == "./hooks/hooks.json"`),
    which is why the manifest shipped in a state that could not install. The
    `hooks` key is only for ADDITIONAL hook files beyond the standard one, so the
    check is on what a declared path RESOLVES to, not on the literal string.
    """
    standard = (ROOT / "hooks" / "hooks.json").resolve()
    assert standard.is_file(), "the auto-loaded file must still exist"
    declared = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text()).get("hooks")
    paths = [declared] if isinstance(declared, str) else (declared or [])
    for p in paths:
        assert (ROOT / p).resolve() != standard, f"{p} duplicates the auto-loaded hooks file"


def test_marketplace_manifest_points_at_the_plugin():
    """`claude plugin install` resolves only through a marketplace.

    Without `.claude-plugin/marketplace.json` at the REPO ROOT there is no way to
    install this plugin at all. It has to live here rather than being symlinked
    in from outside: Claude Code resolves the link and refuses with "Path escapes
    plugin directory".
    """
    mk = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    entry = next(p for p in mk["plugins"] if p["name"] == "tokendog")
    plugin_dir = (REPO / entry["source"]).resolve()
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file(), \
        f"marketplace source {entry['source']} does not contain a plugin manifest"
    # A drifting version installs one thing and reports another.
    manifest = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text())
    assert entry["version"] == manifest["version"]

def test_hooks_json_registers_token_count():
    h = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    events = h["hooks"]
    for ev in ("PreToolUse", "PostToolUse", "Stop", "SessionStart"):
        assert ev in events
        cmd = events[ev][0]["hooks"][0]["command"]
        assert "token_count.py" in cmd

def test_server_module_imports_and_exposes_tools():
    server_path = ROOT / "mcpServers" / "tokendog-cost" / "server.py"
    spec = importlib.util.spec_from_file_location("tokendog_cost_server", server_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "mcp")
    assert callable(mod.cost_summary)
    assert callable(mod.doctor)


SERVER = ROOT / "mcpServers" / "tokendog-cost" / "server.py"


class _StubServer:
    """Stands in for whichever high-level server class the SDK exposes."""

    def __init__(self, name, **kw):
        self.name = name

    def tool(self):
        def decorate(fn):
            return fn
        return decorate


def _load_server_with(monkeypatch, *, mcpserver: bool):
    """Import server.py against a stubbed SDK of one major version.

    mcp 2.x removed `mcp.server.fastmcp` outright, so a real both-versions test
    cannot be done in one environment — but the branch that picks between them
    can be, and that branch is the whole fix.
    """
    pkg = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    pkg.server = server_mod
    monkeypatch.setitem(sys.modules, "mcp", pkg)
    monkeypatch.setitem(sys.modules, "mcp.server", server_mod)
    if mcpserver:  # mcp 2.x: MCPServer present, fastmcp gone
        server_mod.MCPServer = _StubServer
        monkeypatch.delitem(sys.modules, "mcp.server.fastmcp", raising=False)
    else:  # mcp 1.x: no MCPServer, FastMCP under the old path
        fastmcp = types.ModuleType("mcp.server.fastmcp")
        fastmcp.FastMCP = _StubServer
        server_mod.fastmcp = fastmcp
        monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp)
    spec = importlib.util.spec_from_file_location("tokendog_cost_server_probe", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("mcpserver", [True, False], ids=["mcp2", "mcp1"])
def test_server_builds_on_either_sdk_major(monkeypatch, mcpserver):
    """`mcp>=1.2` is unbounded again, so both majors have to work.

    Pinning `<2` was the stopgap; a plugin should not dictate which SDK major
    the host environment installs. Verified against real mcp 1.28.1 and 2.1.1.
    """
    mod = _load_server_with(monkeypatch, mcpserver=mcpserver)
    assert isinstance(mod.mcp, _StubServer)
    assert mod.mcp.name == "tokendog-cost"
    assert callable(mod.cost_summary) and callable(mod.doctor)


def test_dependency_pin_allows_mcp_2():
    """A `<2` cap would silently re-break what this branch fixed."""
    pyproject = (REPO / "pyproject.toml").read_text()
    line = next(l for l in pyproject.splitlines() if l.startswith("dependencies"))
    assert "mcp>=1.2" in line and "<2" not in line


def test_the_two_manifests_declare_the_same_version():
    """`claude plugin update` compares versions, so a stale one is unshippable.

    The installed copy is a snapshot keyed by version: if plugin.json still says
    what the marketplace says, `claude plugin update` reports "already at the
    latest version" and the fix never reaches anyone. `claude plugin tag` rejects
    a mismatch between the two files for the same reason.
    """
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    entries = [p for p in market["plugins"] if p["name"] == plugin["name"]]
    assert entries, "the marketplace does not list this plugin"
    assert entries[0]["version"] == plugin["version"]
