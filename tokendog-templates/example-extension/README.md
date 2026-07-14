# Example org extension

`tokendog init` writes the canonical frugal defaults ABOVE the `<!-- TOKENDOG_EXTENSION_MARKER -->`
and preserves everything BELOW it across re-applies. Put your org-specific instructions below the
marker (see `CLAUDE.md.example`). Re-running `tokendog init` refreshes the defaults without touching
your section.
