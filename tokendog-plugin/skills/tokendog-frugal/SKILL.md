---
name: tokendog-frugal
description: Frugal Claude Code tool-use habits that cut token spend without losing quality. Apply during any coding task involving Read, Grep, Glob, Bash, or MCP tool calls.
when_to_use: Any session that reads files, searches code, runs shell commands, or calls MCP tools — i.e. almost all coding work.
---

# TokenDog — frugal tool use

Spend the fewest tokens that still get the job done. Defaults:

## Reading files
- Prefer `Read` with `offset`+`limit` when you know the region; don't slurp whole large files.
- Don't re-Read a file already in context this session.

## Searching
- Use narrow `Grep` (specific pattern, `path:`/`glob:` scoping, `head_limit`) over broad sweeps.
- Prefer `output_mode: "files_with_matches"` or `"count"` when you only need locations, not content.
- Never dump a whole directory tree when a targeted glob answers the question.

## Bash
- Cap noisy output: pipe through `head`/`tail`, add `--quiet`/`-q`, or `wc -l` first.
- Don't cat large files or logs wholesale — slice to the relevant lines.

## Output discipline (brevity)
- Be brief. No preamble, no summary of what you just did unless asked.
- Prefer structured output (JSON/table) over prose when returning data.

## Session hygiene
- Suggest `/clear` when the topic changes — stale context is paid for on every turn.
- Batch related questions instead of many chatty round-trips.
