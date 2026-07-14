from __future__ import annotations
from pathlib import Path
from tokendog.approx import approx_tokens
from tokendog.truncate import truncate_text


def run_benchmark(fixture_dir, max_lines: int = 200, max_bytes: int = 50_000) -> dict:
    fixture_dir = Path(fixture_dir)
    per = []
    baseline_total = 0
    optimized_total = 0
    for f in sorted(fixture_dir.glob("*.txt")):
        content = f.read_text(encoding="utf-8")
        baseline = approx_tokens(content)
        optimized = approx_tokens(truncate_text(content, max_lines=max_lines, max_bytes=max_bytes)[0])
        per.append({"name": f.name, "baseline": baseline, "optimized": optimized})
        baseline_total += baseline
        optimized_total += optimized
    pct = max(0.0, round((baseline_total - optimized_total) / baseline_total * 100, 1)) if baseline_total else 0.0
    return {
        "baseline_tokens": baseline_total,
        "optimized_tokens": optimized_total,
        "pct_saved": pct,
        "per_fixture": per,
    }
