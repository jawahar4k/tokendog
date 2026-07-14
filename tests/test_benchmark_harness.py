from pathlib import Path
from benchmarks.harness import run_benchmark


def test_reports_savings(tmp_path):
    (tmp_path / "big.txt").write_text("\n".join(str(i) for i in range(2000)))
    (tmp_path / "small.txt").write_text("just a short line")
    r = run_benchmark(tmp_path, max_lines=100)
    assert r["baseline_tokens"] > r["optimized_tokens"]
    assert r["pct_saved"] > 0
    names = {f["name"] for f in r["per_fixture"]}
    assert names == {"big.txt", "small.txt"}
    # the small fixture is unchanged
    small = next(f for f in r["per_fixture"] if f["name"] == "small.txt")
    assert small["baseline"] == small["optimized"]


def test_empty_dir_zero(tmp_path):
    r = run_benchmark(tmp_path)
    assert r["baseline_tokens"] == 0 and r["pct_saved"] == 0.0
