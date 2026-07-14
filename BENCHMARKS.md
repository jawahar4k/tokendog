# Benchmarks

Reproducible token-savings benchmarks for TokenDog's truncation/frugal defaults.

## Run

```bash
.venv/bin/python -m benchmarks.run
```

This measures baseline vs. TokenDog-optimized token counts (tiktoken approximation) across the
fixtures in `benchmarks/fixtures/`. Savings come from the output-truncation defaults
(`TOKENDOG_MAX_LINES` / `TOKENDOG_MAX_BYTES`). Add your own `*.txt` fixtures to reflect your workload.

Note: these measure the *variable-prompt* (tool-output) surface. Cache-pooling and JSON compression
wins land with the gate (Slice 6).
