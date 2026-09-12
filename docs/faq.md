# FAQ

**Are the token numbers exact?** For Claude Code they're tiktoken approximations (~95%), because
Claude Code hooks don't expose token counts. For Glitch they're authoritative (from its stop hook).
The gate (Slice 6+) will capture exact `usage.*` for both.

**Does it slow down my session?** Hooks are fire-and-forget and fail open — any error exits silently
and never blocks work. The only intentional block is a hard budget deny.

**Do I have to run the proxy?** No. The plugin, templates, and toolkit all work standalone. The gate
is optional and adds the transport-layer wins.

**Does it work with Glitch?** Yes — twice over. Glitch pipelines spawn `claude --print`, which writes
normal Claude Code transcripts, so their Claude spend is measured like any other session. And
`tokendog pipelines` attributes that spend to the specific pipeline/run by reading `.glitch/runs/*.json`
(each stage records the session id Glitch handed to `--session-id`, which is the transcript name).

**How do I view the dashboard on another machine?** It binds `127.0.0.1` and is unauthenticated, so
tunnel it rather than exposing it: `ssh -N -L 4320:127.0.0.1:4320 you@host`, then open
`http://localhost:4320` locally.

**`tokendog serve` says "Errno 48 / Address already in use".** Another `tokendog serve` is holding the
port. Stop it with `pkill -f "[t]okendog.report serve"` (the `[t]` keeps pkill from matching its own
command line), or start on another port with `--port`.

**Can I trust the cost-per-PR numbers?** Out of the box the session→commit link is a heuristic
(in-window, same repo, overlapping files). Run `tokendog install-hook` in a repo and it becomes exact:
a `prepare-commit-msg` hook stamps the Claude session id into each commit, so attribution is a lookup,
not a guess. Scoped per machine — it counts the sessions run on that machine.

**Do the reports or dashboard cost me tokens?** No. Every report reads local transcripts and prices
them against list-price weights offline. Nothing is sent to any API, and there is no LLM call.
