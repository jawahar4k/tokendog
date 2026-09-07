# Quickstart

## Install
1. `pip install -e .` (the `tokendog` package)
2. Install the plugin, in Claude Code (`claude plugin ...` works the same from a shell):
   - `/plugin marketplace add .` — the repo root ships `.claude-plugin/marketplace.json`
   - `/plugin install tokendog@tokenwise`

   `install` resolves only through a marketplace, so a bare path does not work.
3. `tokendog init` to drop frugal `CLAUDE.md` + `settings.json` into your repo

## See your spend
Run a session, then `/tokendog:cost` for a per-runtime/tool breakdown, or `/tokendog:doctor`
to check the setup. Set a budget with `/tokendog:budget --set-daily 25`.

TokenDog measures Claude Code and Glitch side by side, both from authoritative usage — Claude Code
transcripts and Glitch's stop hook. Hook events record tool-payload volume only and are never priced.
