---
description: Did an optimization actually reduce tokens? Cost-by-turn-position, cache-TTL economics, and tool-payload sizes before vs after a change.
argument-hint: "[--split YYYY-MM-DD] [--project <name>]"
allowed-tools: Bash
---

# TokenDog — effect

!`python -m tokendog.report effect ${ARGUMENTS}`

The intervention ledger — the measurement counterpart to the recommendations. `bands`, `floor`,
`surface` and `discovery` say what to change; this says whether the change worked.

- **Cost by turn position** shows $/turn and context/turn rising as a session grows — the
  compounding you pay because every token in the window is re-read on every later turn. A high
  late/early multiple is the case for splitting a long stage: same work, less carried context.
- **`--split YYYY-MM-DD`** compares tool-payload sizes before vs on/after that date, per tool, with
  the % change. Run it with the day you made a change (scoped Reads, output truncation, a leaner
  prompt) to see the reduction land — or to catch a tool whose payloads quietly grew.

Read retroactively from Claude Code transcripts, so a change made last week can still be checked;
no hooks or instrumentation required.
