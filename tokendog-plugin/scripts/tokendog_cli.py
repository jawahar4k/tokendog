#!/usr/bin/env python3
"""Run a `tokendog` report from a slash command, under an interpreter that can.

Slash commands used to shell out to `python -m tokendog.report …`. On a machine
with only `python3`, that is "command not found"; on one where `python3` is
not the environment tokendog was installed into, it is ModuleNotFoundError.
Both looked like the plugin was broken. This shim goes through the same
interpreter bootstrap the hooks use, then hands the arguments to the CLI.

Unlike a hook, a slash command SHOULD say when it cannot run: the person
asked for a report and is waiting for it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import ensure_tokendog  # noqa: E402


def main() -> int:
    if not ensure_tokendog():
        print("tokendog is not importable by any Python this plugin can find. "
              "Install it (`pip install -e <repo>` or into `~/.tokendog/venv`), or set "
              "TOKENDOG_PYTHON to the interpreter that has it. `/tokendog:doctor` "
              "explains what was tried.", file=sys.stderr)
        return 1
    from tokendog.report import main as report_main
    return report_main(sys.argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
