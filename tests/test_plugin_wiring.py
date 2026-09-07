import json, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tokendog-plugin"

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
    `hooks` key is only for ADDITIONAL hook files beyond the standard one.
    """
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert m.get("hooks") != "./hooks/hooks.json"
    assert (ROOT / "hooks" / "hooks.json").is_file(), "the auto-loaded file must still exist"

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
