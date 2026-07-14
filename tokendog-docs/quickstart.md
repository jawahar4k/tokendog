# Quickstart

## Install
1. `pip install -e .` (the `tokendog` package)
2. Install the plugin: `/plugin install ./tokendog-plugin` in Claude Code
3. `tokendog init` to drop frugal `CLAUDE.md` + `settings.json` into your repo

## See your spend
Run a session, then `/tokendog:cost` for a per-runtime/tool breakdown, or `/tokendog:doctor`
to check the setup. Set a budget with `/tokendog:budget --set-daily 25`.

TokenDog measures Claude Code (tiktoken approximation) and Glitch (authoritative, via its stop hook)
side by side.
