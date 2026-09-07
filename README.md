# TokenDog 🐕

**The watchdog for your Claude Code and Glitch token spend.** Measure it, then reduce it —
org-wide, without losing output quality. Deterministic core, no ML hand-waving.

> Status: feature-complete across all layers (Python + Rust suites green). **Not yet released** —
> publishing is intentionally on hold. See `docs/BUILD-HISTORY.md`.

## What it does

- **Measures** token cost from authoritative usage — Claude Code transcripts and Glitch's stop hook
  — side by side, attributed by runtime / tool / model / team / context band.
- **Reduces** spend in the order the bill is actually incurred — **carry less** (context re-read on
  every turn), **rewrite less** (cache writes when the prefix churns), then **generate less**
  (output). Via output truncation, session hygiene, budgets + alerts, config templates, MCP-author
  helpers, and an optional Rust transport gate (cache pooling, dedup, compression).

### Where the money goes

Measured over one developer's 30,773 metered turns (10.0B tokens, $8,409):

| Bucket | % of tokens | % of cost |
|---|--:|--:|
| Cache read | 96.6% | **56.5%** |
| Cache write | 3.1% | **34.6%** |
| Output | 0.3% | **9.0%** |
| Fresh input | 0.0% | 0.0% |

Roughly nine-tenths of the bill is context economics: how much you carry, and how often you make
the model pay to rebuild it. Output — the thing most token advice is about — is the last ninth. A
token carried for 50 turns costs more on Opus than a token generated once, and here 50.2% of turns
ran at ≥200k context and accounted for 82.4% of spend.

Those are one machine's numbers; a second seat showed the same ordering with output nearer 15%.
Run `tokendog cost` and `tokendog bands` on your own history before believing any of it — that is
what the measurement half is for. Details in `docs/FEATURES.md`.

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
#   /plugin marketplace add .        # the repo root ships .claude-plugin/marketplace.json
#   /plugin install tokendog@tokenwise
#   tokendog init                    # drop frugal CLAUDE.md + settings into your repo
#   ... run a session ...
#   /tokendog:cost                   # see your spend (4 buckets, not one total)
#   /tokendog:bands                  # see WHERE the tokens are (context size)

# Optional Rust gate:
cd tokendog-gate && cargo test
```

## Safe-by-default rollout

A fresh install only **measures** (and gives conservative frugal/hygiene guidance) — it never
silently alters tool output. Adopt the one content-altering feature, truncation, in three steps:

```bash
# 1. Observe — install and work normally. Zero content alteration.
#    /plugin marketplace add . && /plugin install tokendog@tokenwise
#                                        →   /tokendog:cost   (watch your spend)

# 2. Measure — see what truncation WOULD cut, without changing anything:
export TOKENDOG_TRUNCATE_MODE=shadow
#    ... run some real sessions ...
python -m tokendog.report savings        # projected with-vs-without, per tool/session

# 3. Enforce — only if the projected cuts look safe, turn it on:
export TOKENDOG_TRUNCATE_MODE=enforce
```

`tokendog doctor` shows the live state of every quality-affecting feature, plus sink health.

The telemetry sink is bounded by default: `TOKENDOG_MAX_SINK_MB` (64) caps each day's file and
`TOKENDOG_RETENTION_DAYS` (30) prunes old ones. If the sink ever stops accepting writes — full
disk, read-only mount, cap reached — a SessionStart hook says so instead of letting the cost
reports keep rendering confident numbers from stale data. See
`docs/FEATURES.md` for the full safety posture.

### Which Python runs the hooks

Hooks and the MCP server are launched as `python3 <script>`, and `python3` is whatever is first on
PATH — often *not* the environment `pip install tokendog` wrote to. The hooks find an interpreter
that can import `tokendog` (a `$VIRTUAL_ENV`, a `.venv/` beside your project, the `tokendog`
console script's own interpreter) and re-exec under it, caching the answer in
`~/.tokendog/interpreter`. Set `TOKENDOG_PYTHON` to override.

If nothing on the machine can import `tokendog`, the SessionStart hook says so. It has to: every
other hook fails open and exits 0, so without that message a wrong interpreter looks exactly like
a quiet, well-behaved plugin — one that records nothing and reports `$0.00` forever.

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
