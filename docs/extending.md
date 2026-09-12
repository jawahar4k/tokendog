# Extending TokenDog

TokenDog is customizable without forking, via these extension points:

1. **Overlay skills** — ship a private companion plugin with org-specific always-on skills alongside
   `tokendog-frugal`. They load together; no merge conflict.
2. **CLAUDE.md extension marker** — `tokendog init` writes frugal defaults above the
   `<!-- TOKENDOG_EXTENSION_MARKER -->`; put org instructions below it. Re-running preserves your section.
3. **Cost backend adapter** — the `tokendog-cost` MCP backend is a Python Protocol
   (`ingest`/`query`); implement it to point at your warehouse. The default is in-memory SQLite, rebuilt from the transcripts and hook sink on every report; nothing is persisted.
4. **Runtime adapter** — TokenDog treats `runtime ∈ {claude-code, glitch}` as first-class; a runtime
   adapter lets you add another agent runtime that shares the telemetry spine.

All extensions live outside the TokenDog repo.
