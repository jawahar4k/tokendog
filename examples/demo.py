#!/usr/bin/env python3
"""Populate a throwaway TokenDog state dir with realistic events, then show the reports.

This does NOT touch your real ~/.tokendog — it forces TOKENDOG_HOME to a temp dir
(unless you set one) so you can see cost / audit / savings output with sample data.

    python examples/demo.py

Nothing here runs Claude or Glitch; it writes the same events the hooks would.
"""
from __future__ import annotations
import os
import tempfile

# Isolate state BEFORE importing tokendog so config picks it up.
os.environ.setdefault("TOKENDOG_HOME", tempfile.mkdtemp(prefix="tokendog-demo-"))

from tokendog.event import TokenEvent, RUNTIME_CLAUDE, RUNTIME_GLITCH, now_iso
from tokendog.sink import write_event
from tokendog.savings import record_savings
from tokendog.report import (
    cost_summary, format_rollup, format_savings, doctor_report,
)
from tokendog.savings import savings_summary


def seed():
    # --- Claude Code session: a few tool calls (approximate counts) ---
    cc = "demo-claude-session"
    for tool, tin, tout in [("Read", 320, 1400), ("Bash", 210, 5200),
                            ("Edit", 640, 180), ("Grep", 90, 2600)]:
        write_event(TokenEvent(ts=now_iso(), session_id=cc, runtime=RUNTIME_CLAUDE,
                               event="PostToolUse", tool=tool, model="claude-opus-4-8",
                               input_tokens=tin, output_tokens=tout))

    # --- Glitch run: authoritative counts from its stop hook ---
    write_event(TokenEvent(ts=now_iso(), session_id="demo-glitch-run", runtime=RUNTIME_GLITCH,
                           event="agent_completion", agent="developer-ai",
                           model="claude-opus-4-8", input_tokens=8200, output_tokens=3100))

    # --- Two truncations: what TokenDog did (or would do) to big tool outputs ---
    big_log = "\n".join(f"line {i} of a very chatty command" for i in range(4000))
    record_savings(session_id=cc, tool="Bash", original=big_log,
                   kept=big_log[:800], mode="enforce")          # actually shortened
    record_savings(session_id=cc, tool="Read", original=big_log,
                   kept=big_log[:800], mode="shadow")           # projected only, untouched


def main():
    seed()
    print(f"[demo state dir: {os.environ['TOKENDOG_HOME']}]\n")
    print(doctor_report(os.getcwd()), "\n")
    print(format_rollup(cost_summary(group_by="runtime")), "\n")
    print(format_rollup(cost_summary(group_by="tool")), "\n")
    print(format_savings(savings_summary()))


if __name__ == "__main__":
    main()
