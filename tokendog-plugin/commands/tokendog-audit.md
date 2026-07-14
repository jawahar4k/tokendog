---
description: Audit token spend for the current session (or a given session id), broken down by tool.
argument-hint: "[--session <id>]"
allowed-tools: Bash
---

# TokenDog — audit

!`python -m tokendog.report audit ${ARGUMENTS}`

Review the per-tool breakdown above to see where this session's tokens went. Figures are
local approximations for Claude Code; Glitch rows are authoritative.
