# TokenDog Slice 3 — Config Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the drop-in org-config layer — a canonical `CLAUDE.md.template` (frugal defaults + org extension marker), a `settings.json.template` (model default, tool allowlist, telemetry sink, compaction tuning), MCP-hygiene guidance, a worked example-extension, plus a `tokendog init` CLI / `/tokendog:init` command that installs them into any repo while preserving org customizations on re-apply.

**Architecture:** Canonical templates live in top-level `tokendog-templates/` (a standalone distributable per the spec). A new `src/tokendog/templates.py` module holds all apply/merge logic (idempotent install that preserves everything below the `TOKENDOG_EXTENSION_MARKER`). The plugin's `init` command and `report.py init` subcommand are thin wrappers over it. No new dependencies.

**Tech Stack:** Python 3.11+, stdlib only (`json`, `pathlib`), `pytest`. Reuses the Slice 1/2 `tokendog` package.

## Global Constraints

- Prefix `tokendog` everywhere; state dir `~/.tokendog/`.
- License Apache-2.0; Python 3.11+; **no new dependencies**.
- Commands run in the Slice 1 `.venv` (`.venv/bin/python -m pytest …`).
- The extension marker is the exact string `<!-- TOKENDOG_EXTENSION_MARKER -->`. Everything below it in a target `CLAUDE.md` is org-owned and MUST be preserved across re-applies.
- `settings.json.template` MUST be valid JSON.
- `apply_templates` MUST NOT clobber an existing `.claude/settings.json` unless `force=True` (settings are commonly hand-edited); `CLAUDE.md` is always refreshed above the marker, preserved below.
- TDD; commit after each green task. Reuse existing modules — don't duplicate.

---

### Task 1: Templates module (`templates.py`)

**Files:**
- Create: `src/tokendog/templates.py`
- Test: `tests/test_templates.py`

**Interfaces:**
- Produces:
  - `MARKER = "<!-- TOKENDOG_EXTENSION_MARKER -->"`
  - `repo_templates_dir() -> Path` (resolves `<repo>/tokendog-templates`).
  - `render_claude_md(template_text: str, org_section: str = "") -> str` — ensures the marker is present and appends the preserved org section after it.
  - `apply_templates(templates_dir, target_dir, force: bool = False) -> list[Path]` — writes `target/CLAUDE.md` (refresh above marker, preserve below) and `target/.claude/settings.json` (only if missing or `force`); returns written paths.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_templates.py
from pathlib import Path
from tokendog import templates

def _make_templates(dir: Path):
    dir.mkdir(parents=True, exist_ok=True)
    (dir / "CLAUDE.md.template").write_text(
        "# TokenDog frugality defaults\n- prefer Read offset+limit\n\n" + templates.MARKER + "\n")
    (dir / "settings.json.template").write_text('{"model": "claude-sonnet-4-6"}\n')

def test_apply_writes_both(tmp_path):
    tdir = tmp_path / "templates"; _make_templates(tdir)
    target = tmp_path / "repo"
    written = templates.apply_templates(tdir, target)
    claude = (target / "CLAUDE.md").read_text()
    assert templates.MARKER in claude and "offset+limit" in claude
    assert (target / ".claude" / "settings.json").exists()
    assert len(written) == 2

def test_reapply_preserves_org_section(tmp_path):
    tdir = tmp_path / "templates"; _make_templates(tdir)
    target = tmp_path / "repo"
    templates.apply_templates(tdir, target)
    claude = target / "CLAUDE.md"
    claude.write_text(claude.read_text().rstrip() + "\n# Org: internal services at foo/bar\n")
    # change the template's top section, re-apply
    (tdir / "CLAUDE.md.template").write_text(
        "# TokenDog frugality defaults v2\n- cap Bash at 200 lines\n\n" + templates.MARKER + "\n")
    templates.apply_templates(tdir, target)
    out = claude.read_text()
    assert "v2" in out and "cap Bash" in out      # top refreshed
    assert "internal services at foo/bar" in out  # org section preserved

def test_settings_not_clobbered_without_force(tmp_path):
    tdir = tmp_path / "templates"; _make_templates(tdir)
    target = tmp_path / "repo"
    templates.apply_templates(tdir, target)
    s = target / ".claude" / "settings.json"
    s.write_text('{"model": "custom"}')
    templates.apply_templates(tdir, target)               # no force
    assert '"custom"' in s.read_text()
    templates.apply_templates(tdir, target, force=True)   # force overwrites
    assert "claude-sonnet-4-6" in s.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_templates.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.templates`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/templates.py
from __future__ import annotations
from pathlib import Path

MARKER = "<!-- TOKENDOG_EXTENSION_MARKER -->"

def repo_templates_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tokendog-templates"

def _org_section(existing: str) -> str:
    idx = existing.find(MARKER)
    return "" if idx == -1 else existing[idx + len(MARKER):]

def render_claude_md(template_text: str, org_section: str = "") -> str:
    base = template_text if MARKER in template_text else template_text.rstrip() + "\n\n" + MARKER + "\n"
    if org_section.strip():
        base = base.rstrip() + "\n" + org_section.lstrip("\n")
    return base if base.endswith("\n") else base + "\n"

def apply_templates(templates_dir, target_dir, force: bool = False) -> list[Path]:
    templates_dir = Path(templates_dir)
    target_dir = Path(target_dir)
    written: list[Path] = []

    claude_tpl = (templates_dir / "CLAUDE.md.template").read_text(encoding="utf-8")
    target_claude = target_dir / "CLAUDE.md"
    org = _org_section(target_claude.read_text(encoding="utf-8")) if target_claude.exists() else ""
    target_claude.parent.mkdir(parents=True, exist_ok=True)
    target_claude.write_text(render_claude_md(claude_tpl, org), encoding="utf-8")
    written.append(target_claude)

    settings_tpl = (templates_dir / "settings.json.template").read_text(encoding="utf-8")
    settings = target_dir / ".claude" / "settings.json"
    if force or not settings.exists():
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(settings_tpl, encoding="utf-8")
        written.append(settings)
    return written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_templates.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/templates.py tests/test_templates.py
git commit -m "feat: templates module — idempotent apply, preserves org section below marker"
```

---

### Task 2: Canonical `CLAUDE.md.template`

**Files:**
- Create: `tokendog-templates/CLAUDE.md.template`
- Test: `tests/test_claude_template.py`

**Interfaces:**
- Produces: the canonical frugal instructions ending with the extension marker.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claude_template.py
from pathlib import Path
from tokendog.templates import MARKER
TPL = Path(__file__).resolve().parents[1] / "tokendog-templates" / "CLAUDE.md.template"

def test_template_has_marker_and_frugal_rules():
    txt = TPL.read_text()
    assert MARKER in txt
    low = txt.lower()
    for kw in ("offset", "grep", "bash", "brevity"):
        assert kw in low, f"missing: {kw}"
    # marker is at the end — nothing frugal below it
    assert txt.strip().endswith(MARKER) or txt.rstrip().endswith(MARKER)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_claude_template.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write the template file**

`tokendog-templates/CLAUDE.md.template`:
```markdown
# TokenDog frugality defaults

These defaults reduce Claude Code token spend without hurting quality. Keep them terse.

## Reading & searching
- Prefer `Read` with `offset`+`limit`; don't slurp whole large files, and don't re-read a file already in context.
- Use narrow `Grep` (specific pattern, `path:`/`glob:` scoping, `head_limit`); prefer `files_with_matches`/`count` when you only need locations.
- Never dump a whole directory tree when a targeted glob answers the question.

## Bash
- Cap noisy output: pipe through `head`/`tail`, prefer `--quiet`, `wc -l` before catting large files.

## Output discipline
- Be brief — no preamble or after-the-fact summaries unless asked. Prefer structured output over prose when returning data.

## Session hygiene
- `/clear` when the topic changes. Batch related questions instead of chatty round-trips.

<!-- TOKENDOG_EXTENSION_MARKER -->
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_claude_template.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-templates/CLAUDE.md.template tests/test_claude_template.py
git commit -m "feat: canonical CLAUDE.md.template with extension marker"
```

---

### Task 3: Canonical `settings.json.template`

**Files:**
- Create: `tokendog-templates/settings.json.template`
- Test: `tests/test_settings_template.py`

**Interfaces:**
- Produces: a valid-JSON Claude Code settings baseline — default model, a telemetry-friendly permission posture, and conversation-cleanup tuning.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings_template.py
import json
from pathlib import Path
TPL = Path(__file__).resolve().parents[1] / "tokendog-templates" / "settings.json.template"

def test_valid_json_with_expected_keys():
    data = json.loads(TPL.read_text())
    assert isinstance(data, dict)
    assert "model" in data
    assert "cleanupPeriodDays" in data       # session lifecycle / compaction tuning (item 46)
    assert "permissions" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settings_template.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write the template file**

`tokendog-templates/settings.json.template`:
```json
{
  "model": "claude-sonnet-4-6",
  "cleanupPeriodDays": 7,
  "permissions": {
    "allow": [
      "Read",
      "Grep",
      "Glob",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)"
    ],
    "deny": []
  },
  "env": {
    "TOKENDOG_MAX_LINES": "200",
    "TOKENDOG_MAX_BYTES": "50000"
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settings_template.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-templates/settings.json.template tests/test_settings_template.py
git commit -m "feat: canonical settings.json.template (model default, allowlist, cleanup, truncation env)"
```

---

### Task 4: MCP-hygiene guide + example-extension

**Files:**
- Create: `tokendog-templates/mcp-hygiene.md`
- Create: `tokendog-templates/example-extension/CLAUDE.md.example`
- Create: `tokendog-templates/example-extension/README.md`
- Test: `tests/test_slice3_docs.py`

**Interfaces:**
- Produces: MCP disable-by-default guidance (item 40) and a worked example of how an org layers customizations below the marker (extension mechanism 2).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slice3_docs.py
from pathlib import Path
from tokendog.templates import MARKER
T = Path(__file__).resolve().parents[1] / "tokendog-templates"

def test_mcp_hygiene_present():
    txt = (T / "mcp-hygiene.md").read_text().lower()
    assert "mcp" in txt and "disable" in txt

def test_example_extension_uses_marker():
    ex = (T / "example-extension" / "CLAUDE.md.example").read_text()
    assert MARKER in ex
    # org content appears BELOW the marker
    assert ex.split(MARKER, 1)[1].strip() != ""
    assert (T / "example-extension" / "README.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_slice3_docs.py -q`
Expected: FAIL — files do not exist.

- [ ] **Step 3: Write the files**

`tokendog-templates/mcp-hygiene.md`:
```markdown
# MCP hygiene

Every enabled MCP server adds tool schemas to the fixed prompt — paid on every call. Keep the set lean.

## Rules
- **Disable by default.** Enable an MCP only in repos/sessions that actually use it.
- **Prefer deferred-loading MCPs** (tool schemas load on demand) over always-loaded ones.
- **Audit periodically** with `/tokendog:doctor`, which reports whether a Glitch firmware DB and known MCPs are present.
- **One MCP per capability.** Don't run two servers that do the same job.

## Endorsed baseline
Keep: your source-control, issue-tracker, and docs MCPs. Disable everything else until a task needs it.
```

`tokendog-templates/example-extension/CLAUDE.md.example`:
```markdown
# TokenDog frugality defaults
- (the canonical frugal rules live above the marker and are refreshed by `tokendog init`)

<!-- TOKENDOG_EXTENSION_MARKER -->
# Acme Corp — internal conventions
- Service registry: https://registry.acme.internal
- Prefer the `acme-db` MCP for schema questions.
- Deploy runbook: docs/runbooks/deploy.md
```

`tokendog-templates/example-extension/README.md`:
```markdown
# Example org extension

`tokendog init` writes the canonical frugal defaults ABOVE the `<!-- TOKENDOG_EXTENSION_MARKER -->`
and preserves everything BELOW it across re-applies. Put your org-specific instructions below the
marker (see `CLAUDE.md.example`). Re-running `tokendog init` refreshes the defaults without touching
your section.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_slice3_docs.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-templates/mcp-hygiene.md tokendog-templates/example-extension tests/test_slice3_docs.py
git commit -m "feat: mcp-hygiene guide + worked example-extension"
```

---

### Task 5: `tokendog init` CLI + `/tokendog:init` command

**Files:**
- Modify: `src/tokendog/report.py` (add `init` subcommand)
- Create: `tokendog-plugin/commands/tokendog-init.md`
- Test: `tests/test_init_cli.py`, `tests/test_init_command.py`

**Interfaces:**
- Consumes: `tokendog.templates.apply_templates`, `repo_templates_dir`.
- Produces: `tokendog init [--target DIR] [--force]` subcommand in `report.main` (prints the written paths); a command markdown.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_init_cli.py
from pathlib import Path
from tokendog import report

def test_init_writes_templates(tmp_path, monkeypatch, capsys):
    # point the module at a fixture templates dir
    tdir = tmp_path / "tokendog-templates"; tdir.mkdir()
    from tokendog.templates import MARKER
    (tdir / "CLAUDE.md.template").write_text("# frugal\n- offset\n\n" + MARKER + "\n")
    (tdir / "settings.json.template").write_text('{"model": "claude-sonnet-4-6"}\n')
    monkeypatch.setattr("tokendog.report.repo_templates_dir", lambda: tdir)
    target = tmp_path / "repo"
    rc = report.main(["init", "--target", str(target)])
    assert rc == 0
    assert (target / "CLAUDE.md").exists()
    assert (target / ".claude" / "settings.json").exists()
    assert "CLAUDE.md" in capsys.readouterr().out
```

```python
# tests/test_init_command.py
from pathlib import Path
CMD = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "commands" / "tokendog-init.md"

def test_init_command_present():
    txt = CMD.read_text()
    assert txt.startswith("---") and "tokendog.report init" in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_init_cli.py tests/test_init_command.py -q`
Expected: FAIL — subcommand/file missing.

- [ ] **Step 3a: Add the `init` subcommand to `report.py`**

Add import near the other `tokendog` imports in `src/tokendog/report.py`:
```python
from .templates import apply_templates, repo_templates_dir
```

In `main()`, register the subcommand (alongside `cost`/`doctor`/`budget`/`audit`):
```python
    i = sub.add_parser("init")
    i.add_argument("--target", default=".")
    i.add_argument("--force", action="store_true")
```

And handle it in the dispatch chain:
```python
    elif args.cmd == "init":
        written = apply_templates(repo_templates_dir(), args.target, force=args.force)
        for p in written:
            print(f"wrote {p}")
        print("Edit CLAUDE.md below the TOKENDOG_EXTENSION_MARKER for org-specific instructions.")
```

- [ ] **Step 3b: Write the command file**

`tokendog-plugin/commands/tokendog-init.md`:
```markdown
---
description: Install TokenDog's canonical CLAUDE.md + settings.json into this repo (preserves your org section).
argument-hint: "[--force]"
allowed-tools: Bash
---

# TokenDog — init

!`python -m tokendog.report init --target "${CLAUDE_PROJECT_DIR:-.}" ${ARGUMENTS}`

The frugal defaults are written above the `TOKENDOG_EXTENSION_MARKER`; anything below it is your
org's and is preserved on re-run. Add `--force` to also overwrite `.claude/settings.json`.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_init_cli.py tests/test_init_command.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/report.py tokendog-plugin/commands/tokendog-init.md tests/test_init_cli.py tests/test_init_command.py
git commit -m "feat: tokendog init CLI + /tokendog:init command"
```

---

### Task 6: Register the init command + full suite

**Files:**
- Modify: `tokendog-plugin/.claude-plugin/plugin.json`
- Test: `tests/test_slice3_wiring.py`

**Interfaces:**
- Produces: `plugin.json` lists `./commands/tokendog-init.md` alongside the existing four commands.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slice3_wiring.py
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / "tokendog-plugin"

def test_init_command_registered():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert "./commands/tokendog-init.md" in m["commands"]
    # earlier commands still present
    assert "./commands/tokendog-cost.md" in m["commands"]
    assert "./commands/tokendog-budget.md" in m["commands"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_slice3_wiring.py -q`
Expected: FAIL — init not registered.

- [ ] **Step 3: Update the manifest**

In `tokendog-plugin/.claude-plugin/plugin.json`, add `"./commands/tokendog-init.md"` to the `commands` array (keep the existing four).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all Slice 1 + 2 + 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/.claude-plugin/plugin.json tests/test_slice3_wiring.py
git commit -m "feat: register /tokendog:init in plugin manifest"
```

---

## Exit criterion (Slice 3 done)

`tokendog init` (and `/tokendog:init`) drops a frugal `CLAUDE.md` + `.claude/settings.json` into a target repo; re-running refreshes the defaults above the marker while preserving the org section below it, and won't clobber a hand-edited `settings.json` without `--force`. MCP-hygiene guidance and a worked example-extension exist. Full suite green.

## Self-review notes

- **Spec coverage (spec §5 Slice 3):** CLAUDE.md.template (Task 2, items 6/38) · settings.json.template (Task 3, items 39/23/46) · mcp-hygiene.md (Task 4, item 40) · example-extension + marker (Task 4, extension mechanism 2) · apply/merge logic + init command (Tasks 1/5). Item 22 (static model tier) is documentation-level and covered by the settings `model` default + the frugal skill; no code needed.
- **Placeholders:** none — every step has runnable code/content.
- **Type consistency:** `MARKER`, `apply_templates(templates_dir, target_dir, force)`, `repo_templates_dir()` identical across Tasks 1/5; `report.main` `init` subcommand extends the existing parser without renaming prior subcommands.
- **Idempotency guarantee:** re-apply preserves everything below the marker (Task 1 test) and never clobbers settings.json without `force` (Task 1 test).
