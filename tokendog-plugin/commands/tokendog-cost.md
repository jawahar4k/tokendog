---
description: Show a TokenDog token-spend rollup (Claude Code + Glitch) for this environment.
argument-hint: "[group-by: runtime|user|tool|model|day]"
allowed-tools: Bash
---

# TokenDog — cost

Here is the current token-spend rollup (grouped by `$ARGUMENTS`, default `runtime`):

!`python -m tokendog.report cost --group-by "${ARGUMENTS:-runtime}" --glitch-db "${CLAUDE_PROJECT_DIR:-$(pwd)}/.glitch/firmware/firmware.db"`

Present the table above to the user. Note that figures are **local approximations**
(tiktoken), not authoritative billing — authoritative numbers arrive once the
TokenDog gate is deployed. If the table is empty, tell the user to run a few tool
calls first so telemetry can accumulate.
