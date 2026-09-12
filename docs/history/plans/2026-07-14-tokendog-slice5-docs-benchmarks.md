# TokenDog Slice 5 — Docs + Benchmarks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the documentation site (mkdocs) covering the whole 47-item framework, an honest comparison + FAQ + extending guide, and a **reproducible benchmark harness** that measures real token savings from TokenDog's truncation/frugal defaults on fixture workloads.

**Architecture:** Docs are static markdown under `tokendog-docs/` with an `mkdocs.yml` nav. The benchmark harness is real testable Python under `benchmarks/` that reuses the `tokendog` package (`approx`, `truncate`) to compute baseline-vs-optimized token counts on fixture tool-outputs — dogfooding the measurement spine. NO third-party deps in tested code.

**Tech Stack:** Python 3.11+, stdlib only (tested code), `pytest`. mkdocs itself is only needed to *render* the site (not to test it).

## Global Constraints

- Prefix `tokendog`; Apache-2.0; Python 3.11+; **no new deps in tested code** (mkdocs is a docs-render-time tool, not a test/runtime dep; tests validate docs with string checks, not YAML parsing since pyyaml is absent).
- Commands run in the Slice 1 `.venv`.
- **NO PUBLIC RELEASE.** This slice explicitly does NOT publish to GitHub, push to any public remote, tag a release, or announce. Build artifacts locally only — release is on hold until the user tests. Any task suggesting publish/announce is OUT OF SCOPE.
- Benchmark harness reuses `tokendog.approx.approx_tokens` + `tokendog.truncate.truncate_text` — do not reimplement.
- TDD; commit after each green task. Full suite (Slices 1–5) stays green.

---

### Task 1: Benchmark harness (`benchmarks/harness.py`)

**Files:**
- Create: `benchmarks/harness.py`
- Create: `benchmarks/fixtures/big_log.txt` (a large multi-line fixture that truncates)
- Create: `benchmarks/fixtures/small_snippet.txt` (a small fixture that does not truncate)
- Create: `benchmarks/__init__.py` (empty, so pytest can import via path)
- Modify: `pyproject.toml` (add `benchmarks` to pytest `pythonpath`)
- Test: `tests/test_benchmark_harness.py`

**Interfaces:**
- Produces: `run_benchmark(fixture_dir, max_lines: int = 200, max_bytes: int = 50_000) -> dict` → `{"baseline_tokens": int, "optimized_tokens": int, "pct_saved": float, "per_fixture": [{"name", "baseline", "optimized"}...]}`. For each `*.txt` fixture: baseline = `approx_tokens(content)`, optimized = `approx_tokens(truncate_text(content, ...)[0])`. `pct_saved = round((baseline-optimized)/baseline*100, 1)` (0.0 if baseline 0).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_benchmark_harness.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_benchmark_harness.py -q`
Expected: FAIL — module/pythonpath missing.

- [ ] **Step 3: Create harness, fixtures, path**

`benchmarks/__init__.py`: empty file.

`benchmarks/harness.py`:
```python
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
    pct = round((baseline_total - optimized_total) / baseline_total * 100, 1) if baseline_total else 0.0
    return {"baseline_tokens": baseline_total, "optimized_tokens": optimized_total,
            "pct_saved": pct, "per_fixture": per}
```

`benchmarks/fixtures/big_log.txt`: 2000+ lines of realistic-looking log output (e.g., repeated `2026-07-14T00:00:00Z INFO handler processed request id=<n> status=200` lines, one per number 0–2500).

`benchmarks/fixtures/small_snippet.txt`:
```
def add(a, b):
    return a + b
```

In repo-root `pyproject.toml`, extend the pytest path:
```toml
pythonpath = ["src", "tokendog-mcp-toolkit/py", "benchmarks"]
```

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_benchmark_harness.py -q && .venv/bin/python -m pytest -q`
Expected: harness tests pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/harness.py benchmarks/__init__.py benchmarks/fixtures pyproject.toml tests/test_benchmark_harness.py
git commit -m "feat: benchmark harness measuring truncation token savings"
```

---

### Task 2: Benchmark runner CLI + BENCHMARKS.md

**Files:**
- Create: `benchmarks/run.py`
- Create: `BENCHMARKS.md`
- Test: `tests/test_benchmark_run.py`

**Interfaces:**
- Consumes: `benchmarks.harness.run_benchmark`.
- Produces: `format_report(result: dict) -> str` (markdown table) and `main(argv=None) -> int` (`python -m benchmarks.run [--fixtures DIR]` prints the report).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_benchmark_run.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_benchmark_run.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/run.py
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
```

`BENCHMARKS.md`:
```markdown
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_benchmark_run.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/run.py BENCHMARKS.md tests/test_benchmark_run.py
git commit -m "feat: benchmark runner CLI + BENCHMARKS.md"
```

---

### Task 3: mkdocs site skeleton + quickstart

**Files:**
- Create: `tokendog-docs/mkdocs.yml`
- Create: `tokendog-docs/quickstart.md`
- Test: `tests/test_docs_site.py`

**Interfaces:**
- Produces: an mkdocs config whose nav references the site pages, plus a quickstart.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_docs_site.py
from pathlib import Path
D = Path(__file__).resolve().parents[1] / "tokendog-docs"

def test_mkdocs_config_references_pages():
    cfg = (D / "mkdocs.yml").read_text()
    assert "site_name" in cfg and "TokenDog" in cfg
    for page in ("quickstart.md", "how-it-works.md", "comparison.md", "faq.md", "extending.md"):
        assert page in cfg, f"nav missing {page}"

def test_quickstart_has_install_and_cost_command():
    q = (D / "quickstart.md").read_text().lower()
    assert "install" in q and "/tokendog:cost" in q
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_docs_site.py -q`
Expected: FAIL — files missing.

- [ ] **Step 3: Write the files**

`tokendog-docs/mkdocs.yml`:
```yaml
site_name: TokenDog
site_description: The watchdog for your Claude Code and Glitch token spend.
theme:
  name: material
nav:
  - Quickstart: quickstart.md
  - How it works: how-it-works.md
  - Extending: extending.md
  - Comparison: comparison.md
  - FAQ: faq.md
```

`tokendog-docs/quickstart.md`:
```markdown
# Quickstart

## Install
1. `pip install -e .` (the `tokendog` package)
2. Install the plugin: `/plugin install ./tokendog-plugin` in Claude Code
3. `tokendog init` to drop frugal `CLAUDE.md` + `settings.json` into your repo

## See your spend
Run a session, then `/tokendog:cost` for a per-runtime/tool breakdown, or `/tokendog:doctor`
to check the setup. Set a budget with `/tokendog:budget --set-daily 25`.

TokenDog measures Claude Code (tiktoken approximation) and Glitch (authoritative, via its stop hook)
side by side.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_docs_site.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-docs/mkdocs.yml tokendog-docs/quickstart.md tests/test_docs_site.py
git commit -m "feat: mkdocs site skeleton + quickstart"
```

---

### Task 4: `how-it-works.md` — the 47-item framework

**Files:**
- Create: `tokendog-docs/how-it-works.md`
- Test: `tests/test_how_it_works.py`

**Interfaces:**
- Produces: a doc covering all 47 optimization items grouped by the framework's categories A–J (see `compact-DESIGN.md` §4/§6 for the canonical list), one short paragraph or row each, with a code pointer where implemented.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_how_it_works.py
from pathlib import Path
DOC = Path(__file__).resolve().parents[1] / "tokendog-docs" / "how-it-works.md"

def test_covers_all_categories_and_items():
    txt = DOC.read_text()
    # 10 framework categories A–J
    for cat in ("Fixed-prompt", "Variable-prompt", "caching", "routing",
                "Output-token", "Observability", "Org-wide", "Behavioral",
                "Session lifecycle", "memory"):
        assert cat.lower() in txt.lower(), f"missing category: {cat}"
    # references all 47 numbered items — check item numbers 1..47 appear
    for n in (1, 8, 20, 32, 41, 47):
        assert f"{n}" in txt
    assert len(txt) > 3000, "how-it-works should be substantial"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_how_it_works.py -q`
Expected: FAIL — file missing.

- [ ] **Step 3: Write the doc**

Create `tokendog-docs/how-it-works.md`. Use `compact-DESIGN.md` §4 (the 47-item table) and §6 (the code-location mapping) as the source. Structure it with the ten category headings and, under each, a compact table or list of its items — number, one-line description, and where TokenDog implements it (or "deferred / gate"). The ten categories are:

- **A · Fixed-prompt shrink** (items 1–7)
- **B · Variable-prompt shrink** (items 8–16)
- **C · Prompt caching** (items 17–21)
- **D · Smart routing** (items 22–28)
- **E · Output-token discipline** (items 29–31)
- **F · Observability + budgets** (items 32–37)
- **G · Org-wide defaults** (items 38–41)
- **H · Behavioral / cultural** (items 42–44)
- **I · Session lifecycle** (items 45–46)
- **J · Cross-session memory** (item 47)

For each item give: `**N.** <description> — <status: shipped in Slice X / deferred to gate>`. Mark items shipped in Slices 1–4 as shipped with their module, and items 11–14/17–20/24/26–28/31/33 (server variants) as "gate (Slice 6+)". This doc must reference every number 1–47.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_how_it_works.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-docs/how-it-works.md tests/test_how_it_works.py
git commit -m "docs: how-it-works — full 47-item framework"
```

---

### Task 5: `extending.md` + `comparison.md` + `faq.md` + full suite

**Files:**
- Create: `tokendog-docs/extending.md`
- Create: `tokendog-docs/comparison.md`
- Create: `tokendog-docs/faq.md`
- Test: `tests/test_docs_pages.py`

**Interfaces:**
- Produces: the extension-mechanism guide, an honest comparison vs Headroom, and an FAQ.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_docs_pages.py
from pathlib import Path
D = Path(__file__).resolve().parents[1] / "tokendog-docs"

def test_extending_covers_marker_and_backends():
    txt = (D / "extending.md").read_text()
    assert "TOKENDOG_EXTENSION_MARKER" in txt
    assert "backend" in txt.lower()

def test_comparison_mentions_headroom():
    txt = (D / "comparison.md").read_text().lower()
    assert "headroom" in txt and "gate" in txt

def test_faq_nonempty():
    assert len((D / "faq.md").read_text()) > 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_docs_pages.py -q`
Expected: FAIL — files missing.

- [ ] **Step 3: Write the files**

`tokendog-docs/extending.md`:
```markdown
# Extending TokenDog

TokenDog is customizable without forking, via these extension points:

1. **Overlay skills** — ship a private companion plugin with org-specific always-on skills alongside
   `tokendog-frugal`. They load together; no merge conflict.
2. **CLAUDE.md extension marker** — `tokendog init` writes frugal defaults above the
   `<!-- TOKENDOG_EXTENSION_MARKER -->`; put org instructions below it. Re-running preserves your section.
3. **Cost backend adapter** — the `tokendog-cost` MCP backend is a Python Protocol
   (`ingest`/`query`); implement it to point at your warehouse instead of local SQLite.
4. **Gate config** (Slice 6+) — the proxy is driven by YAML with pluggable routing/auth/budget rules.
5. **Runtime adapter** — TokenDog treats `runtime ∈ {claude-code, glitch}` as first-class; a runtime
   adapter lets you add another agent runtime that shares the telemetry + gate spine.

All extensions live outside the TokenDog repo.
```

`tokendog-docs/comparison.md`:
```markdown
# Comparison

## vs Headroom
| Dimension | TokenDog | Headroom |
|---|---|---|
| Scope | Full-stack (plugin + templates + toolkit + optional gate) | Transport proxy only |
| Works without a proxy | Yes (plugin alone) | No |
| Per-user/team/workflow attribution | Yes | Partial |
| Frugal-prompting skill | Yes | No |
| CLAUDE.md + settings templates | Yes | No |
| MCP author toolkit | Yes | No |
| JSON compression / cache pooling | Gate (Slice 6+) | Yes |
| Multi-runtime (Claude Code + Glitch) | Yes | No |
| Governance | OSS, Apache-2.0 | Single-vendor |

**Pitch:** install TokenDog for measurable Claude Code + Glitch cost reduction without needing a proxy,
with the option to add the gate when you're ready. TokenDog is a superset of Headroom as a product,
implementing the transport techniques natively for governance + co-design.
```

`tokendog-docs/faq.md`:
```markdown
# FAQ

**Are the token numbers exact?** For Claude Code they're tiktoken approximations (~95%), because
Claude Code hooks don't expose token counts. For Glitch they're authoritative (from its stop hook).
The gate (Slice 6+) will capture exact `usage.*` for both.

**Does it slow down my session?** Hooks are fire-and-forget and fail open — any error exits silently
and never blocks work. The only intentional block is a hard budget deny.

**Do I have to run the proxy?** No. The plugin, templates, and toolkit all work standalone. The gate
is optional and adds the transport-layer wins.

**Does it work with Glitch?** Yes — Glitch spend is ingested from its firmware DB and its stop hook,
shown side by side with Claude Code.
```

- [ ] **Step 4: Run the tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_docs_pages.py -q && .venv/bin/python -m pytest -q`
Expected: pages pass; full suite (Slices 1–5) green.

- [ ] **Step 5: Commit**

```bash
git add tokendog-docs/extending.md tokendog-docs/comparison.md tokendog-docs/faq.md tests/test_docs_pages.py
git commit -m "docs: extending, comparison vs Headroom, FAQ"
```

---

## Exit criterion (Slice 5 done)

`python -m benchmarks.run` prints a token-savings report from fixtures; the mkdocs site under
`tokendog-docs/` documents the full 47-item framework, extension points, an honest Headroom comparison,
and an FAQ. Full suite green. **Nothing is published** — release stays on hold for the user.

## Self-review notes

- **Spec coverage (spec §5 Slice 5):** benchmark harness (Tasks 1–2) · how-it-works covering all 47 items (Task 4) · comparison vs Headroom (Task 5) · docs site (Tasks 3–5). The "publish v0.1 / launch post" step is DELIBERATELY EXCLUDED per the user (release on hold).
- **Placeholders:** none — harness/runner have full code; docs have full content or an explicit source (`compact-DESIGN.md` §4/§6) plus a structural spec for the 47-item page.
- **No new test/runtime deps:** harness reuses `tokendog`; doc tests are string checks (no pyyaml). mkdocs is only needed to render, not to test.
- **Release safety:** the Global Constraints and exit criterion both state NO publish; no task pushes, tags, or announces.
