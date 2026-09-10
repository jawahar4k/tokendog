# Comparison

## vs Headroom
| Dimension | TokenDog | Headroom |
|---|---|---|
| Scope | Full-stack (plugin + templates + toolkit + optional gate) | Transport proxy only |
| Works without a proxy | Yes (plugin alone) | No |
| Per-user/team/workflow attribution | Yes | Partial |
| Frugal-prompting skill | Yes | No |
| CLAUDE.md + settings templates | Yes | No |
| MCP author toolkit | Yes | No |
| JSON compression / cache pooling | Gate (Slice 6+) | Yes |
| Multi-runtime (Claude Code + Glitch) | Yes | No |
| Governance | OSS, Apache-2.0 | Single-vendor |

**Pitch:** install TokenDog for measurable Claude Code + Glitch cost reduction without needing a proxy,
with the option to add the gate when you're ready. TokenDog is a superset of Headroom as a product,
and will implement the transport techniques natively for governance + co-design when the gate ships (Slice 6+).

## vs tuneloop

tuneloop answers "was my AI spend worth it" (cost per merged PR, success rate, autonomy over time);
TokenDog answers "why did my limit just vanish, and what do I disable or reset". They overlap on
outcome attribution and tool-error surfacing, which TokenDog now covers deterministically.

| Dimension | TokenDog | tuneloop |
|---|---|---|
| Primary lens | Context economics / rate-limit forensics | Outcome / ROI, retrospective |
| Cost per merged PR | Yes (`outcomes`) | Yes |
| Spend per pipeline | Yes (`pipelines`, reads `.glitch/runs`) | — |
| Tool error rates | Yes (`errors`) | Yes |
| Time-window forensics (`--since`/`--until`), burn rate, occupancy bands | Yes | — |
| Exact attribution | Commit trailer (`install-hook`) — no LLM | LLM-enriched |
| Needs an LLM / its own token spend | **No** — deterministic, free, instant | Yes (~$ per run) |
| Actuator (turn a connector off) | Yes (`surface --disable`) | — |
| Harnesses | Claude Code + Glitch | Claude Code, Codex, OpenCode, Pi |

**Pitch:** for the rate-limit problem, TokenDog is the better fit and adds nothing to the bill —
every number comes from transcripts you already have. tuneloop adds multi-harness support and
LLM-derived qualitative themes (work type, complexity, re-steer patterns) that TokenDog deliberately
omits, since paying tokens to analyse a token limit is the wrong trade.
