# Contributing to TokenDog

Thanks for your interest in improving TokenDog. Contributions of all kinds are welcome —
bug reports, docs, tests, and code.

## Development setup

```bash
# Python package + suite
pip install -e ".[dev]"
python -m pytest -q

# Optional Rust transport gate
cd tokendog-gate && cargo test
```

Pytest resolves `src`, `tokendog-mcp-toolkit/py`, and `benchmarks` via `pyproject.toml`.

## Ground rules

- **Test-driven.** Add a failing test first, then the minimal code to pass it. Keep the suite green.
- **Small, focused changes.** One concern per pull request; keep modules single-responsibility.
- **No unnecessary dependencies.** The core package uses `tiktoken` + `mcp`; the MCP toolkit and gate
  are stdlib / `serde_json` only.
- **Hooks must fail open.** A hook script must never crash or block the host session — on any error it
  exits 0 and emits nothing. The only intentional block is the budget-enforcement deny (which also
  fails open on error).
- **Honest naming and docs.** Don't claim capabilities the code doesn't have (e.g. "neural"/semantic
  features that aren't actually implemented). Mark deferred work as deferred.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`,
`chore:`, `refactor:`. Write a clear, imperative subject line.

## Pull requests

1. Fork and branch from `main`.
2. Make your change with tests; run the full suite.
3. Open a PR describing the change and its motivation.

## Reporting issues

Please open a GitHub issue. For security-sensitive reports, see `CODE_OF_CONDUCT.md`.

## License

By contributing, you agree that your contributions are licensed under the Apache License 2.0.
