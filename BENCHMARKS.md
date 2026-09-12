# Benchmarks

Reproducible per-payload benchmarks for the tool-output condenser (opt-in, off by default).

## Run

```bash
.venv/bin/python -m benchmarks.run
```

This measures baseline vs. TokenDog-optimized token counts (tiktoken approximation) across the
fixtures in `benchmarks/fixtures/`. Add your own `*.txt` fixtures to reflect your workload.

These are per-payload figures: how much smaller one tool result gets. They say nothing about the
share of your bill those payloads are — on a measured coding workload it was small, because the large
payloads were files the model asked to read, which the condenser never cuts. `tokendog condense
--replay` runs the same condenser over your real transcripts and shows what it would drop.
