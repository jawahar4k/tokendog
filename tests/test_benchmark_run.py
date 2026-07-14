from benchmarks import run

def test_format_report():
    result = {"baseline_tokens": 1000, "optimized_tokens": 300, "pct_saved": 70.0,
              "per_fixture": [{"name": "big.txt", "baseline": 1000, "optimized": 300}]}
    out = run.format_report(result)
    assert "70.0%" in out and "big.txt" in out and "| Fixture" in out

def test_main_runs(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("\n".join(str(i) for i in range(500)))
    rc = run.main(["--fixtures", str(tmp_path)])
    assert rc == 0 and "%" in capsys.readouterr().out
