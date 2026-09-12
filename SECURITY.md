# Security

## What TokenDog touches

- **Reads:** your Claude Code transcripts under `~/.claude/projects` (every project on the machine),
  its own state under `~/.tokendog`, and, if present, Glitch's `firmware.db`.
- **Writes:** only under `~/.tokendog`, plus `settings.json` edits you explicitly confirm
  (`surface --disable`, `tokendog init`), each backed up first.
- **Network:** none by default. Two opt-in paths send data off the machine: the budget webhook
  you configure, and the condenser's Haiku worker, which only runs with a key you set and only in
  `enforce` mode. Neither is on unless you turn it on.
- **Dashboard:** binds `127.0.0.1` and is unauthenticated. Do not expose it; tunnel over SSH.
- **Publishes:** session ids and project directory basenames. Never absolute paths, never
  conversation content.

## Reporting a vulnerability

Open a private security advisory on GitHub, or email the address on
[jawaharprasad.com](https://jawaharprasad.com). Please do not file public issues for security
problems. You will get an acknowledgement within a few days.
