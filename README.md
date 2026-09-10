# TokenDog 🐕

**The watchdog for your coding-agent token spend.** Measure it, then reduce it —
org-wide, without losing output quality. Deterministic core, no ML hand-waving.

> Status: feature-complete across all layers (Python + Rust suites green). **Not yet released** —
> publishing is intentionally on hold. See `docs/BUILD-HISTORY.md`.

## What it does

- **Measures** token cost from authoritative usage — agent transcripts and Glitch's stop hook
  — side by side, attributed by runtime / tool / model / team / context band.
- **Reduces** spend in the order the bill is actually incurred — **carry less** (context re-read on
  every turn), **rewrite less** (cache writes when the prefix churns), then **generate less**
  (output). Via output truncation, session hygiene, budgets + alerts, config templates, MCP-author
  helpers, and an optional Rust transport gate (cache pooling, dedup, compression).

### Where the money goes

Measured over one developer's 19,466 metered turns (6.33B tokens, $4,884) across 255 transcripts:

| Bucket | % of tokens | % of cost |
|---|--:|--:|
| Cache read | 97.4% | **62.2%** |
| Cache write | 2.3% | **28.9%** |
| Output | 0.3% | **8.9%** |
| Fresh input | 0.0% | 0.0% |

Roughly nine-tenths of the bill is context economics: how much you carry, and how often you make
the model pay to rebuild it. Output — the thing most token advice is about — is the last ninth. A
token carried for 50 turns costs more on Opus than a token generated once, and here 50.9% of turns
ran at ≥200k context and carried 84.4% of all context tokens.

`Est $` is **API list-price attribution, not an invoice** — on a Pro/Max subscription there is no
per-token charge, so read it as relative weight. It is not a rate-limit proxy either: it applies
price weights (output 5× input, cache read 0.1×) that quota accounting does not.

Transcripts cover every project on the machine, so scope a report with `--project <name>` (the
project directory's basename); `tokendog doctor` lists the names it found.

Those are one machine's numbers; a second seat showed the same ordering with output nearer 15%.
Run `tokendog cost` and `tokendog bands` on your own history before believing any of it — that is
what the measurement half is for. Details in `docs/FEATURES.md`.

## Layers (each independently useful)

- **`src/tokendog/` + `tokendog-plugin/`** — the Python package and agent plugin: telemetry hook,
  cost MCP, frugal + hygiene skills, and slash commands `/tokendog:cost` `:doctor` `:budget` `:audit` `:init`.
- **Local dashboard** (`tokendog serve`) — the same figures as a page (see [Dashboard](#dashboard)),
  in tabs: Overview (window occupancy, daily trend, ranked act-now list), Connectors, Recommendations,
  Cost (the four billing buckets by model + per-tool payload), Pipelines (Claude spend per Glitch
  pipeline), Outcomes (cost per merged PR), Tool errors, Findings, and a per-session Detail drawer.
  One global range control (Today / Yesterday / 7d / 14d / 30d / 90d / All) scopes every tab; each
  headline compares against the previous window of the same length. Colourblind-safe by construction
  (status is shape + word, never colour alone). Standard-library HTTP, loopback-bound, no web framework.
- **Context surface** (`tokendog surface`) — what every prompt carries before the conversation
  starts, grouped by connector because that is the unit you can switch off. Names the connectors
  that are resident on every turn and never called, and the idle tools inside the ones you do use.
  `--disable` turns one off: dry run by default, backed up, reversible.
- **Statusline** (`tokendog-plugin/scripts/statusline.py`) — window occupancy, session age and the
  5-hour / 7-day limit percentages on screen while you work. Installed by `tokendog init`.
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

# In the agent:
#   /plugin marketplace add .        # the repo root ships .claude-plugin/marketplace.json
#   /plugin install tokendog@tokenwise
#   tokendog init                    # drop frugal CLAUDE.md + settings into your repo
#   ... run a session ...
#   /tokendog:cost                   # see your spend (4 buckets, not one total)
#   /tokendog:bands                  # see WHERE the tokens are (context size)
#   tokendog cost --project myapp    # one project, not the whole machine
#   tokendog audit                   # payload volume per tool
#   tokendog hygiene                 # was each session managed, and what did drift cost
#   tokendog resumes                 # windows carried past the moment to reset them
#   tokendog coldstart               # discovery paid for more than once
#   tokendog surface                 # what is in the window before you type: connectors,
#                                    #   which of their tools you actually call, what is idle
#   tokendog surface --refresh       # start each connector to size its tool schemas
#   tokendog surface --disable NAME  # dry run; add --apply to make the edit (backed up)
#   tokendog session <id>            # turn-by-turn: what entered the window, and what it cost
#   tokendog session <id> --json     # ...as JSON, to hand to something else
#   tokendog install-hook           # stamp session id into commits → EXACT PR attribution
#   tokendog errors                  # which tools/MCP fail most, and how
#   tokendog pipelines               # Claude spend per Glitch pipeline (reads .glitch/runs)
#   tokendog discovery               # finding vs doing — flags the grep-loop sessions
#   tokendog floor                   # context floor budget: MCPs/skills/instructions, sized + used-or-not
#   tokendog outcomes                # cost per merged PR — links sessions→commits→PRs
#   tokendog outcomes --gh           # resolve PRs via GitHub CLI (squash-merge repos)
#   tokendog serve                   # all of it as a local page on 127.0.0.1:4320

# Any turn-measuring report takes a time range. This is the one to reach for when
# a limit has JUST gone: a rate limit is spent in minutes, and a calendar day is
# three orders of magnitude too coarse to see it.
#   tokendog hygiene --since 17:29 --until 17:45   # the sixteen minutes that did it
#   tokendog hygiene --since 19:20                 # everything since the bucket reset
#   tokendog bands   --since 90m                   # a span back from now
#   tokendog session <id> --since 17:29 --until 17:45
#   ...accepted by bands, resumes, coldstart, hygiene, surface and session.
#   Forms: 17:29 · 2026-09-08 · 2026-09-08T17:29 · 90m / 3h / 2d / 1w · today · yesterday
#   Clock times and dates are LOCAL. `--since 23:50` typed at 00:10 means ten
#   minutes ago, not a window in the future.

# Optional Rust gate:
cd tokendog-gate && cargo test
```

## Environment toggles

- `TOKENDOG_QUIET=1` — silence the end-of-turn spend summary (the `Stop says: TokenDog…` line). Token counting and budget alerts still run; only the message is muted.
- `TOKENDOG_STATUSLINE_FLOOR=0` — hide the **baseline** segment (on by default): the always-on tokens (MCP schemas + skills + instructions, by size) to the statusline. Off by default. Note: the live ctx figure can't be split by source — Claude Code's statusline payload doesn't carry that — so this shows the fixed baseline, not a slice of the total. Use `tokendog floor` for the full itemised budget.
- `TOKENDOG_OBSERVE_ONLY=1` — the budget hook never denies a tool call, only watches.

## Dashboard

Everything the CLI reports, as one local page — most people only ever open this.

```bash
tokendog serve                 # binds 127.0.0.1:4320 by default
tokendog serve --port 4321     # if 4320 is taken (see the note below)
```

Then open **http://localhost:4320**. Tabs across the top — Overview, Connectors,
Recommendations, Cost, Pipelines, Outcomes, Tool errors, Findings, Detail — and a
single range control in its own row above them (Today / Yesterday / 7d … / All)
that scopes every tab and shows the exact from→to dates. Click any session id for
a turn-by-turn **drawer** (or its `json` link for the machine-readable view). The
git-backed tabs (Cost / Pipelines / Outcomes / Tool errors) compute lazily when you
first open them, so the page loads instantly.

**Viewing it from another machine.** The server binds loopback and is
**unauthenticated** — it exposes project names, session ids and token counts — so
don't expose it on a routable interface. To see a dashboard running on a remote box
(a build server, another laptop), forward the port over SSH and browse locally:

```bash
ssh -N -L 4320:127.0.0.1:4320 you@remote-host      # leave this running
#   then open http://localhost:4320 on your own machine
```

`--address 0.0.0.0` exists for the reverse-proxy case, but the SSH tunnel is the
safe default.

**"Errno 48 / Address already in use."** A `tokendog serve` is already holding the
port. Find and stop it, or use another port:

```bash
pkill -f "[t]okendog.report serve"      # the [t] stops pkill matching its own command
tokendog serve --port 4321
```

### Running without an editable install

On a box where you don't want to `pip install`, tokendog is pure standard library
(only `tiktoken` is optional, for exact payload sizing — it falls back to a byte
estimate). Point `PYTHONPATH` at `src` and run the module:

```bash
PYTHONPATH=/path/to/tokenwise/src python3 -m tokendog.report serve --port 4320
# or a tiny launcher:
#   export PYTHONPATH="$HOME/tokendog/src"; export TOKENDOG_HOME="$HOME/tokendog/state"
#   python3 -m tokendog.report "$@"
```

### Exact PR / pipeline attribution (optional)

`tokendog outcomes` links sessions to merged PRs and `tokendog pipelines` links them
to Glitch pipeline runs. Both work heuristically out of the box; for **exact**
attribution, install the commit hook so each commit records the session that made it:

```bash
tokendog install-hook            # in a repo; adds a prepare-commit-msg hook (reversible: --remove)
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
and ships a Glitch-native stop hook for authoritative counts, showing Glitch and agent spend in one
view. It integrates with Glitch's existing memory rather than duplicating it.

## Docs

- `CLAUDE.md` — working guidance for agents in this repo
- `docs/BUILD-HISTORY.md` — how it was built + key decisions
- `docs/superpowers/` — per-slice specs and plans
- `tokendog-docs/` — the full framework (mkdocs)
- `compact-DESIGN.md` — the original design brief

## License

Apache-2.0.
