# TokenDog 🐕

**The watchdog for your Claude Code and Glitch token spend.** Measure it, then reduce it —
org-wide, without losing output quality. Deterministic core, no ML hand-waving.

> Status: feature-complete across all layers (Python + Rust suites green). **Not yet released** —
> publishing is intentionally on hold. See `docs/BUILD-HISTORY.md`.

## What it does

- **Measures** every tool call's token cost — Claude Code (tiktoken approximation) and Glitch
  (authoritative, via its stop hook) — side by side, attributed by runtime / tool / model / team.
- **Reduces** spend with frugal defaults, output truncation, budgets + alerts, MCP-author helpers,
  config templates, and an optional Rust transport gate (compression, cache pooling, dedup).

## Layers (each independently useful)

- **`src/tokendog/` + `tokendog-plugin/`** — the Python package and Claude Code plugin: telemetry hook,
  cost MCP, frugal + hygiene skills, and slash commands `/tokendog:cost` `:doctor` `:budget` `:audit` `:init`.
- **`tokendog-templates/`** — drop-in `CLAUDE.md` + `settings.json` baselines (`tokendog init`), with an
  extension marker that preserves your org's customizations across updates.
- **`tokendog-mcp-toolkit/`** — the `tokendog_mcp` package: pagination, truncation, batch-dedup, dense
  schemas, and deferred tool loading for MCP authors.
- **`tokendog-docs/`** — mkdocs site documenting the full 47-item optimization framework.
- **`benchmarks/`** — a reproducible token-savings harness.
- **`tokendog-gate/`** — optional Rust transport-gate transforms (compression, canonical ordering +
  cache-key pooling, dedup, usage capture, budgets, CCR, session persistence, Q→A cache).

## Quickstart

```bash
pip install -e ".[dev]"
python -m pytest -q                 # Python suite
python -m benchmarks.run            # token-savings benchmark

# In Claude Code:
#   /plugin install ./tokendog-plugin
#   tokendog init                    # drop frugal CLAUDE.md + settings into your repo
#   ... run a session ...
#   /tokendog:cost                   # see your spend

# Optional Rust gate:
cd tokendog-gate && cargo test
```

## Safe-by-default rollout

A fresh install only **measures** (and gives conservative frugal/hygiene guidance) — it never
silently alters tool output. Adopt the one content-altering feature, truncation, in three steps:

```bash
# 1. Observe — install and work normally. Zero content alteration.
#    /plugin install ./tokendog-plugin   →   /tokendog:cost   (watch your spend)

# 2. Measure — see what truncation WOULD cut, without changing anything:
export TOKENDOG_TRUNCATE_MODE=shadow
#    ... run some real sessions ...
python -m tokendog.report savings        # projected with-vs-without, per tool/session

# 3. Enforce — only if the projected cuts look safe, turn it on:
export TOKENDOG_TRUNCATE_MODE=enforce
```

`tokendog doctor` shows the live state of every quality-affecting feature. See
`docs/FEATURES.md` for the full safety posture.

## Works with Glitch

TokenDog treats Glitch as a first-class second runtime: it ingests Glitch's `firmware.db` `context_log`
and ships a Glitch-native stop hook for authoritative counts, showing Glitch and Claude Code spend in one
view. It integrates with Glitch's existing memory rather than duplicating it.

## Docs

- `CLAUDE.md` — working guidance for agents in this repo
- `docs/BUILD-HISTORY.md` — how it was built + key decisions
- `docs/superpowers/` — per-slice specs and plans
- `tokendog-docs/` — the full framework (mkdocs)
- `compact-DESIGN.md` — the original design brief

## License

Apache-2.0.
