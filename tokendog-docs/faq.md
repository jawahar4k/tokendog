# FAQ

**Are the token numbers exact?** For Claude Code they're tiktoken approximations (~95%), because
Claude Code hooks don't expose token counts. For Glitch they're authoritative (from its stop hook).
The gate (Slice 6+) will capture exact `usage.*` for both.

**Does it slow down my session?** Hooks are fire-and-forget and fail open — any error exits silently
and never blocks work. The only intentional block is a hard budget deny.

**Do I have to run the proxy?** No. The plugin, templates, and toolkit all work standalone. The gate
is optional and adds the transport-layer wins.

**Does it work with Glitch?** Yes — Glitch spend is ingested from its firmware DB and its stop hook,
shown side by side with Claude Code.
