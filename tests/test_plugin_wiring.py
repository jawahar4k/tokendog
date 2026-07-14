import json, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tokendog-plugin"

def test_plugin_manifest_valid():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert m["name"] == "tokendog"
    assert m["hooks"] == "./hooks/hooks.json"
    assert "tokendog-cost" in m["mcpServers"]

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
