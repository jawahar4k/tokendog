---
description: Show a TokenDog token-spend rollup (Claude Code + Glitch) for this environment.
argument-hint: "[group-by: runtime|user|tool|model|day]"
allowed-tools: Bash
---

# TokenDog — cost

Here is the current token-spend rollup (grouped by `$ARGUMENTS`, default `runtime`):

!`python -m tokendog.report cost --group-by "${ARGUMENTS:-runtime}" --glitch-db "${CLAUDE_PROJECT_DIR:-$(pwd)}/.glitch/firmware/firmware.db"`

Present the table above to the user, including its footer.

The figures are **authoritative**: they come from per-turn `message.usage` in the
Claude Code transcripts, which is what the API actually metered. Do not describe
them as approximations — that was true before transcript ingestion and is not now.

Do carry the footer's caveat: `Est $` is API list-price attribution, not an
invoice. On a Pro/Max subscription there is no per-token charge, so it reads as
relative weight rather than money owed — and it is not a rate-limit proxy, because
it applies price weights that quota accounting does not.

Transcripts cover every project on the machine. If the user asks about one project,
re-run with `--project <name>`; `tokendog doctor` lists the available names.

If the table is empty, run `tokendog doctor` — the usual cause is that the
transcript directory was not found, not that there is no spend.
