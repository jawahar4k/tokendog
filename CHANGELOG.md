# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Initial development is complete across all layers; a tagged release has not yet been published.

### Added

- **Measurement** — token telemetry event model, tiktoken-based approximation, JSONL sink, SQLite cost
  backend with grouped roll-ups, cost estimation, and a reporting CLI. Cross-runtime attribution
  (Claude Code + Glitch) is built in.
- **Claude Code plugin** — a crash-proof telemetry hook, a `tokendog-cost` MCP server, and the slash
  commands `/tokendog:cost`, `:doctor`, `:budget`, `:audit`, `:init`.
- **Guardrails** — per-day/session budgets with a fail-open enforcement hook, output truncation, an
  end-of-session spend summary, and webhook budget alerts.
- **Frugal behavior** — always-on frugal and session-hygiene skills; a Glitch-native stop hook that
  records authoritative token counts.
- **Config templates** — idempotent `CLAUDE.md` and `settings.json` baselines installed via
  `tokendog init`, preserving org customizations below an extension marker.
- **MCP author toolkit** (`tokendog_mcp`) — pagination, response truncation, batch-query dedup, dense
  tool schemas, and deferred tool loading.
- **Docs & benchmarks** — an mkdocs site covering the full optimization framework and a reproducible
  token-savings benchmark harness.
- **Transport gate** (`tokendog-gate`, Rust) — JSON compression, canonical ordering + pooled cache
  keys, context dedup, usage parsing, budget checks, reversible compress-cache-retrieve, head+tail
  truncation, session persistence, and an exact-match Q→A cache.

### Deferred

- Live HTTPS proxy wiring, SQLite-backed CCR, embedding-based semantic cache, local model offload, and
  Anthropic Memory API integration (documented in `tokendog-gate/README.md`).
