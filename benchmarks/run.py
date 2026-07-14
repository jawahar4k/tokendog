from __future__ import annotations
import argparse
from pathlib import Path
from .harness import run_benchmark


def format_report(result: dict) -> str:
    lines = ["# TokenDog benchmark", "",
             f"**Total tokens saved: {result['pct_saved']}%** "
             f"({result['baseline_tokens']} → {result['optimized_tokens']})", "",
             "| Fixture | Baseline | Optimized | Saved |", "|---|--:|--:|--:|"]
    for f in result["per_fixture"]:
        saved = f["baseline"] - f["optimized"]
        lines.append(f"| {f['name']} | {f['baseline']} | {f['optimized']} | {saved} |")
    lines.append("")
    lines.append("_Approximate (tiktoken) token counts; savings come from output truncation defaults._")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="benchmarks.run")
    p.add_argument("--fixtures", default=str(Path(__file__).parent / "fixtures"))
    args = p.parse_args(argv)
    print(format_report(run_benchmark(args.fixtures)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
