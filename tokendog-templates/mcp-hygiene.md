# MCP hygiene

Every enabled MCP server adds tool schemas to the fixed prompt — paid on every call. Keep the set lean.

## Rules
- **Disable by default.** Enable an MCP only in repos/sessions that actually use it.
- **Prefer deferred-loading MCPs** (tool schemas load on demand) over always-loaded ones.
- **Audit periodically** with `/tokendog:doctor`, which reports whether a Glitch firmware DB and known MCPs are present.
- **One MCP per capability.** Don't run two servers that do the same job.

## Endorsed baseline
Keep: your source-control, issue-tracker, and docs MCPs. Disable everything else until a task needs it.
