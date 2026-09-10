# CLAUDE.md — TokenDog

Guidance for coding agents working in this repo.

## What this is

**TokenDog** — an open-source framework that measures and reduces coding-agent / Glitch token
spend across a developer org (target 30–60% reduction, no quality loss). The name is a **watchdog**
metaphor: it watches token spend and barks when budgets are exceeded. The core is **deterministic**
(static routing, statistical compression, cache pooling, budgets) — there are no ML/"neural" claims.

Design brief: `compact-DESIGN.md` (the original 47-item framework; "compact" was a placeholder name).
Build history + decisions: `docs/BUILD-HISTORY.md`. Per-slice specs/plans: `docs/superpowers/`.

## Layers (each independently useful)

| Path | What |
|---|---|
| `src/tokendog/` | Python package: telemetry event model, tiktoken approximation, JSONL sink, SQLite cost backend, ingestion (incl. Glitch), reporting/CLI, budgets, truncation, templates |
| `tokendog-plugin/` | Agent plugin: hooks, `tokendog-cost` MCP, frugal + hygiene skills, slash commands (`/tokendog:cost` `:doctor` `:budget` `:audit` `:init`), Glitch stop hook |
| `tokendog-templates/` | Drop-in `CLAUDE.md` + `settings.json` baselines, mcp-hygiene, example-extension |
| `tokendog-mcp-toolkit/` | Standalone `tokendog_mcp` package: frugal helpers for MCP authors |
| `tokendog-docs/` | mkdocs site documenting the full 47-item framework |
| `benchmarks/` | Reproducible token-savings harness |
| `tokendog-gate/` | Optional Rust transport-gate transforms (compression, cache pooling, dedup, usage, CCR, etc.) |

## Build & test

```bash
pip install -e ".[dev]"            # install the Python package
python -m pytest -q                # Python suite (src + toolkit + benchmarks + plugin wiring)
python -m tokendog.report cost     # cost rollup CLI
python -m tokendog.report hygiene --since 17:29 --until 17:45   # scoped to a time range
python -m benchmarks.run           # savings benchmark

cd tokendog-gate && cargo test     # Rust gate suite
```

Pytest resolves `src`, `tokendog-mcp-toolkit/py`, and `benchmarks` via `pyproject.toml` `pythonpath`.

## Conventions

- Identifier prefix is **`tokendog`** everywhere; never `compact`. State dir `~/.tokendog/` (override `TOKENDOG_HOME`).
- Python 3.11+; runtime deps kept to `tiktoken` + `mcp`; the MCP toolkit and gate are stdlib/serde_json only.
- **Hook scripts must never crash the host session:** on any error exit 0 and emit nothing. The only
  intentional block is `budget_enforce`'s explicit PreToolUse deny (which itself fails open on error).
- Every `TokenEvent` sets `runtime` = `"claude-code"` or `"glitch"`. Cross-runtime attribution fields
  (`runtime`, `pipeline`, `run_id`, `agent`, `cluster`) are part of the schema — don't drop them.
- **Cost comes only from authoritative usage.** Agent transcripts (`message.usage`) and Glitch's
  stop hook are authoritative and are what get priced. Hook events carry a tiktoken approximation of
  tool-payload *volume* (`tool_payload_tokens`) and are never priced — those bytes are already billed
  by the turn that carries them. `TokenEvent.source` records which is which.
- **Never collapse a billing dimension the source already provides.** The ephemeral 5m/1h cache split
  must survive ingestion: the two TTLs bill at different multipliers (1.25× vs 2× input), so the
  `cache_creation_input_tokens` scalar alone cannot be priced exactly. Same for `service_tier` and
  `inference_geo` — both affect price and neither is derivable downstream.
- Price all four buckets (fresh input, output, cache read, cache write). Cache is the large majority
  of a real agent bill; omitting it is not a rounding error.
- **A hook that fails open must still be observable.** Hooks exit 0 and emit nothing on error, so
  every failure mode they swallow needs somewhere that reports it — otherwise "broken" and
  "healthy" look identical. `sink_health` (SessionStart) is that place: it covers a dead sink and
  an interpreter that cannot import `tokendog`. Adding a new swallowed failure means adding it there.
- **Never assume `python3` is the environment tokendog was installed into.** Hook and MCP entry
  points go through `scripts/_bootstrap.py`, which re-execs under a working interpreter before
  reading stdin (a re-exec inherits an unread fd 0; it cannot put back consumed bytes).
- The MCP server supports both `mcp` majors — `mcp.server.MCPServer` (2.x) falling back to
  `mcp.server.fastmcp.FastMCP` (1.x). A plugin should not dictate the host's SDK major.
- TDD; small focused modules; commit after each green change. When a test encodes a bug's absence,
  verify it FAILS with the fix reverted — this repo has shipped green suites over broken code.

## Status

Slices 1–7 are complete (Python + Rust suites green). **Not yet released** — publishing is intentionally
on hold. Documented follow-ups (need network/a model) live in `tokendog-gate/README.md`: live HTTPS proxy
wiring, SQLite-backed CCR, embedding-based semantic cache, local Ollama offload, Anthropic Memory API.

## Glitch interop (important)

TokenDog is a first-class second runtime for **Glitch** as well as the coding agent. Glitch has native
agent-lifecycle hooks whose `stop` hook exposes authoritative token counts; TokenDog ships a Glitch
stop hook and ingests Glitch's `firmware.db` `context_log`. Integrate with Glitch's memory rather than
duplicating it. See `docs/BUILD-HISTORY.md` for details.
