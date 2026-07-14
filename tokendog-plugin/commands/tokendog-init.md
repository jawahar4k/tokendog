---
description: Install TokenDog's canonical CLAUDE.md + settings.json into this repo (preserves your org section).
argument-hint: "[--force]"
allowed-tools: Bash
---

# TokenDog — init

!`python -m tokendog.report init --target "${CLAUDE_PROJECT_DIR:-.}" "${ARGUMENTS}"`

The frugal defaults are written above the `TOKENDOG_EXTENSION_MARKER`; anything below it is your
org's and is preserved on re-run. Add `--force` to also overwrite `.claude/settings.json`.
