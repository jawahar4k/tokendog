#!/usr/bin/env python3
"""tokendog-cost MCP server: expose token-spend rollups to Claude Code.
Requires `pip install tokendog`. Reads the JSONL sink + Glitch firmware.db (cwd)."""
import os
import sys

# Same interpreter problem the hooks have — `python3` is often not the
# environment tokendog was installed into — but here it is fatal rather than
# silent: the server dies with ModuleNotFoundError and the MCP tools simply
# never appear. Re-exec under an interpreter that works before importing.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "scripts"))
try:
    from _bootstrap import ensure_tokendog
except Exception:  # pragma: no cover - bootstrap absent; import and see
    def ensure_tokendog() -> bool:
        return True
ensure_tokendog()

try:
    # mcp 2.x renamed FastMCP to MCPServer and removed the old import path.
    # Support both: a plugin should not dictate which SDK major the host env has.
    from mcp.server import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from tokendog import report

mcp = _Server("tokendog-cost")

def _glitch_db() -> str | None:
    path = os.path.join(os.getcwd(), ".glitch", "firmware", "firmware.db")
    return path if os.path.exists(path) else None

@mcp.tool()
def cost_summary(group_by: str = "runtime", since: str | None = None,
                 until: str | None = None) -> dict:
    """Token-spend rollup grouped by runtime|user|tool|model|pipeline|day|session_id|agent|cluster."""
    return report.cost_summary(group_by=group_by, since=since, until=until,
                               glitch_db=_glitch_db())

@mcp.tool()
def doctor() -> str:
    """Environment check: state dir, event count, tiktoken availability, Glitch DB presence."""
    return report.doctor_report(os.getcwd())

if __name__ == "__main__":
    mcp.run()
