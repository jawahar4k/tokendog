---
name: tokendog-hygiene
description: Session-hygiene practices that stop token spend from ballooning over long sessions. Use when a session has run long, switched topics, or accumulated large pasted context.
when_to_use: Long or multi-topic sessions; when the user pastes large logs; when context has grown and cost per turn is climbing.
---

# TokenDog — session hygiene

Every turn pays for the whole conversation. Keep it lean:

- **Clear on topic switch.** When the task changes, suggest `/clear`. Carrying an unrelated 50-turn history taxes every future turn.
- **Don't paste giant logs.** Ask for the relevant slice (the failing lines, the specific stack frame), not a 5,000-line dump. Point at a file to `Read` with `offset`+`limit` instead.
- **Batch questions.** Group related asks into one turn rather than many chatty round-trips — each round re-sends the whole context.
- **Prune stale attachments.** If a large file or tool result is no longer needed, don't keep re-referencing it.
