---
description: TokenDog environment check — telemetry sink, tiktoken, Glitch DB.
allowed-tools: Bash
---

# TokenDog — doctor

!`python -m tokendog.report doctor`

Review the report above. If tiktoken is MISSING, tell the user to `pip install tiktoken`
for accurate approximations. If the event count is 0, telemetry hasn't recorded yet —
confirm the plugin is installed and the hooks are firing.
