# Quickstart

## Install
1. `pip install -e .` (the `tokendog` package)
2. Install the plugin, in Claude Code (`claude plugin ...` works the same from a shell):
   - `/plugin marketplace add .` — the repo root ships `.claude-plugin/marketplace.json`
   - `/plugin install tokendog@tokendog`

   `install` resolves only through a marketplace, so a bare path does not work.
3. `tokendog init` to drop frugal `CLAUDE.md` + `settings.json` into your repo

## See your spend
Run a session, then `/tokendog:cost` for a per-runtime/tool breakdown, or `/tokendog:doctor`
to check the setup. Set a budget with `/tokendog:budget --set-daily 25`.

TokenDog prices every metered turn from authoritative usage in the Claude Code transcripts,
interactive and `claude --print` alike. Hook events record tool-payload volume only and are never
priced. If you also run Glitch pipelines, its stop hook is a second authoritative source.

## The reports
Every report reads your transcripts — no API key, no LLM call, nothing sent anywhere.

```bash
tokendog bands        # where the tokens are: turns grouped by how full the window was
tokendog hygiene      # was each session managed; burn rate, resets, and the excess it carried
tokendog resumes      # windows carried past an obvious moment to reset
tokendog coldstart    # discovery paid for more than once across headless runs
tokendog surface      # what every prompt carries before you type (connectors, idle tools)
tokendog session <id> # turn-by-turn drilldown of one session
tokendog errors       # which tools / MCP servers fail most, and how
tokendog outcomes     # cost per merged PR (links sessions -> commits -> PRs)
tokendog pipelines    # Claude spend per Glitch pipeline (reads .glitch/runs)
tokendog discovery    # finding vs doing — flags grep-loop sessions that needed a map
tokendog floor        # context floor budget: MCPs/skills/instructions, sized + used-or-not
```

Any turn-measuring report takes a **time range** — the tool to reach for when a limit has just
gone (a rate limit is spent in minutes; a calendar day is far too coarse):

```bash
tokendog hygiene --since 17:29 --until 17:45   # the sixteen minutes that did it
tokendog bands   --since 90m                   # a span back from now
# forms: 17:29 · 2026-09-08 · 2026-09-08T17:29 · 90m/3h/2d/1w · today · yesterday  (all local time)
```

## Dashboard
All of it as one local page — most people only open this.

```bash
tokendog serve                 # binds 127.0.0.1:4320
tokendog serve --port 4321     # if 4320 is taken
```

Open **http://localhost:4320**. Tabs — Overview, Connectors, Recommendations, Cost, Pipelines,
Outcomes, Tool errors, Findings, Detail — with one range control above them (Today / Yesterday /
7d … / All) that scopes every tab and shows the exact from→to dates. Click any session id for a
turn-by-turn drawer. It's colourblind-safe (status is a shape + a word, never colour alone).

**View a dashboard on a remote box** over an SSH tunnel — it binds loopback and is unauthenticated,
so never expose it directly:

```bash
ssh -N -L 4320:127.0.0.1:4320 you@remote-host     # then open http://localhost:4320 locally
```

**Errno 48 / address in use** means a `tokendog serve` already holds the port:
`pkill -f "[t]okendog.report serve"` (the `[t]` stops pkill matching itself), or use `--port`.

## Exact PR / pipeline attribution (optional)
`outcomes` and `pipelines` work heuristically out of the box. For exact links, install the commit
hook so each commit records the session that made it — no guessing by time or file overlap:

```bash
tokendog install-hook          # in a repo; reversible with --remove
```

## Without a pip install
TokenDog is pure standard library (`tiktoken` is optional). On a box where you don't want to
install, point `PYTHONPATH` at `src`:

```bash
PYTHONPATH=/path/to/tokendog/src python3 -m tokendog.report serve
```
