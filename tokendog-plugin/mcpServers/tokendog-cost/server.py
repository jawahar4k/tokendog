#!/usr/bin/env python3
"""tokendog-cost MCP server: expose token-spend rollups to Claude Code.
Requires `pip install tokendog`. Reads the JSONL sink + Glitch firmware.db (cwd)."""
import os
import sys

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
