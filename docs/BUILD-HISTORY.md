# TokenDog — Build History & Decisions

A durable record of how TokenDog was built, and the decisions behind it, for future sessions and
contributors. (Captures what was previously only in session memory.)

## Overview

TokenDog was built in **7 slices**, each following the same workflow: a written spec/plan (under
`docs/superpowers/`), then implementation via a **dogfooded Glitch pipeline** (multi-agent, TDD, with
automated code-review → QA → retrospective → fix-loop), then human/agent verification of the exit
criteria. Every slice's code is test-covered; the Python and Rust suites are green.

## Key decisions

- **Name — TokenDog.** A "watchdog for your token spend." Chosen over the placeholder `compact`
  (which collides with Claude Code's built-in `/compact` command) and over hype names. An earlier
  candidate "NeuralToken" was rejected because the product's core is deterministic — there was no
  genuine neural component, and the name would have implied a capability that doesn't exist. Naming
  should be honest to what the thing actually does.
- **Deterministic core, no unearned "neural."** Static routing, statistical compression, cache
  pooling, budgets. Where a "semantic" feature would need embeddings (e.g. semantic Q→A cache), it is
  implemented as exact/normalized match and the embedding version is documented as a follow-up.
- **Plugin-first.** The plugin, templates, and toolkit all work standalone; the transport gate is
  optional.
- **Multi-runtime.** TokenDog reduces and attributes spend for **both** Claude Code and Glitch. Glitch
  is not just a consumer — it already has agent-lifecycle hooks (its `stop` hook exposes authoritative
  `TOKENS_INPUT`/`TOKENS_OUTPUT`/`MODEL`), a `firmware.db` code+memory index, and embedding-based
  memory. TokenDog integrates with these rather than duplicating them.
- **License:** Apache-2.0. **Gate language:** Rust. **Ollama:** shell-out (not embedded).
- **Release:** deliberately on hold until maintainer testing. Nothing is published/announced.

## Slice-by-slice

| Slice | Deliverable | Notes |
|---|---|---|
| 1 · Measurement spine | `tokendog` pkg (event, tiktoken approx, JSONL sink, SQLite backend, report/CLI) + plugin (telemetry hook, cost MCP, `/tokendog:cost` `:doctor`) + Glitch `context_log` ingest | "Measure before optimize." Cross-runtime schema from day one. |
| 2 · Frugal + guardrails | budgets, output truncation, session summary, webhook alerts, Glitch stop hook (authoritative counts), frugal + hygiene skills, `/budget` `/audit` | Hooks fail open; only budget enforce can deny. |
| 3 · Config templates | idempotent `templates.py` (preserves org section below the extension marker), `CLAUDE.md`/`settings.json` templates, mcp-hygiene, `/tokendog:init` | Re-apply refreshes defaults, keeps org customizations. |
| 4 · MCP toolkit | standalone `tokendog_mcp`: pagination, truncation, batch-dedup, dense schema, deferred registry + pattern docs | Framework-agnostic, stdlib-only. |
| 5 · Docs + benchmarks | mkdocs site (all 47 items), Headroom comparison, FAQ; benchmark harness | Benchmark shows large truncation savings on log-shaped fixtures. **No release performed.** |
| 6 · Rust gate core | `tokendog-gate` crate: compress, canonical ordering + cache pooling, dedup, usage parse, budgets + CLI | serde_json-only; live proxy scoped as follow-up. |
| 7 · Advanced gate | CCR reversible retrieve, head+tail truncation, session persistence, exact-match Q→A cache | Semantic cache, SQLite CCR, Ollama, Memory API documented as follow-ups. |

## Review findings the pipeline caught (examples)

The automated review stages found and fixed real bugs that were baked into the plans, including:
- **Input double-counting** in the telemetry hook (fired on both Pre/PostToolUse) — fixed by branching
  on `hook_event_name`.
- **Corrupt-JSONL crash** in `read_events` — fixed with a per-line guard.
- **`audit --session`** printing the wrong group header — fixed.
- **Negative token values** slipping through the Glitch `_int` helper — fixed with `max(0, …)`.
- **Mispriced mixed-model rollups** (`MAX(model)`) — fixed by storing per-row cost at ingest.

## Tooling notes

- Built via Glitch pipelines run with `--auto-approve --headless`. A Glitch bug was found: on headless
  runs, `pipeline approve --stage <gate>` signals are discarded ("without 'stage' field"), wedging a
  run in `awaiting_approval` (which `pause`/`resume` both refuse). Workaround: run with `--auto-approve`
  from the start (approves gates internally, bypassing the external-signal path). Worth a fix in Glitch.
- Local `source: repo` runs are tracked by `glitch pipeline status/history`; they do not appear in the
  Command Center Runs tab (that reads the registry, which this project hadn't synced).

## Deferred / follow-ups (not built)

- **Public v0.1 release** — on hold pending maintainer testing.
- **Live HTTPS proxy** wiring for the gate (terminate TLS, forward, stream, capture usage, enforce budgets).
- **SQLite-backed CCR**, **embedding-based semantic cache**, **local Ollama offload**, **Anthropic
  Memory API** — all noted in `tokendog-gate/README.md`.
- **TypeScript mirror** of the MCP toolkit.
