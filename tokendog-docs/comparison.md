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
