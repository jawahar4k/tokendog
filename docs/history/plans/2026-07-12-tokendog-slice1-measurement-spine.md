# TokenDog Slice 1 — Measurement Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the foundation that lets a developer *see* their Claude Code (and Glitch) token spend — a telemetry hook that records per-tool-call token approximations to a JSONL sink, a SQLite-backed cost roll-up, a `/tokendog:cost` report, and a `/tokendog:doctor` env check.

**Architecture:** A small installable Python package `tokendog` holds all reusable logic (event model, tiktoken approximation, JSONL sink, SQLite backend, ingestion, reporting). A thin Claude Code plugin (`tokendog-plugin`) wires that logic to the runtime: a `PreToolUse`/`PostToolUse`/`Stop`/`SessionStart` hook script emits events, an MCP server + two slash commands surface the roll-up. Token counts aren't provided by Claude Code hooks, so we approximate locally with tiktoken (Path A from the brief). Glitch spend is ingested from its `.glitch/firmware/firmware.db` `context_log` table (runtime-tagged), making the schema cross-runtime from day one.

**Tech Stack:** Python 3.11+, `tiktoken` (approximation), `mcp` (MCP server SDK), stdlib `sqlite3`/`dataclasses`/`json`, `pytest`.

## Global Constraints

- Product/identifier prefix is **`tokendog`** everywhere (never `compact`). CLI/commands `/tokendog:*`; state dir `~/.tokendog/` (overridable via `TOKENDOG_HOME`).
- License: **Apache-2.0**.
- Python **3.11+** (uses `X | None` syntax, `from __future__ import annotations` where helpful).
- Every `TokenEvent` MUST set `runtime` to `"claude-code"` or `"glitch"` — no unset runtime.
- Token numbers from hooks are **approximations** (tiktoken `cl100k_base`); label them as such in any user-facing text. Authoritative counts arrive later via the gate (Slice 6).
- Hook scripts MUST NEVER crash a Claude Code session: on any error they exit 0 and emit nothing.
- Cost prices in `pricing.py` are **configurable defaults, not authoritative** — tests assert arithmetic, never specific price values.
- TDD: write the failing test first. Commit after each green task.
- Dependencies stay minimal: `tiktoken`, `mcp`. No pydantic, no ORM.
- All Python commands run inside a project **`.venv`** created in Task 1 (avoids PEP-668 "externally-managed" pip failures on Homebrew Python). Invoke as `.venv/bin/python -m pytest …` / `.venv/bin/python -m pip …` (or activate the venv first). `.venv/` is git-ignored.

---

### Task 1: Repo scaffold + installable package

**Files:**
- Create: `LICENSE` (Apache-2.0 full text)
- Create: `README.md`
- Create: `pyproject.toml`
- Create: `src/tokendog/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`

**Interfaces:**
- Produces: an installable package `tokendog` (`pip install -e .`) and a working `pytest` invocation.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "tokendog"
version = "0.1.0"
description = "TokenDog — watch and reduce Claude Code / Glitch token spend"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
dependencies = ["tiktoken>=0.7", "mcp>=1.2"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
tokendog = "tokendog.report:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package + test markers, `.gitignore`, README stub, LICENSE**

`src/tokendog/__init__.py`:
```python
"""TokenDog — token-spend telemetry and cost reduction for Claude Code and Glitch."""
__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

`.gitignore`:
```
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
build/
dist/
.tokendog/
.venv/
```

`README.md`:
```markdown
# TokenDog 🐕

The watchdog for your Claude Code and Glitch token spend. Measures, then reduces.

## Slice 1 — Measurement spine
Install: `pip install -e ".[dev]"` then install the plugin at `tokendog-plugin/`.
Run `/tokendog:cost` in Claude Code to see your spend.
```

`LICENSE`: full Apache-2.0 text (fetch canonical text; header line `Apache License, Version 2.0`).

- [ ] **Step 3: Create venv, install, verify empty test run**

Run: `python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]" && .venv/bin/python -m pytest -q`
Expected: venv created; install succeeds; pytest reports `no tests ran` (exit 5) — acceptable at this step.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src tests LICENSE README.md .gitignore
git commit -m "chore: scaffold tokendog package (Apache-2.0, pytest)"
```

---

### Task 2: Config paths (`config.py`)

**Files:**
- Create: `src/tokendog/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `tokendog_home() -> Path`, `telemetry_dir() -> Path`, `db_path() -> Path`. All honor `TOKENDOG_HOME` env override.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path
from tokendog import config

def test_home_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert config.tokendog_home() == tmp_path

def test_telemetry_dir_created(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    d = config.telemetry_dir()
    assert d == tmp_path / "telemetry"
    assert d.is_dir()

def test_default_home_when_unset(monkeypatch):
    monkeypatch.delenv("TOKENDOG_HOME", raising=False)
    assert config.tokendog_home() == Path.home() / ".tokendog"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.config`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/config.py
from __future__ import annotations
import os
from pathlib import Path

def tokendog_home() -> Path:
    root = os.environ.get("TOKENDOG_HOME")
    return Path(root).expanduser() if root else Path.home() / ".tokendog"

def telemetry_dir() -> Path:
    d = tokendog_home() / "telemetry"
    d.mkdir(parents=True, exist_ok=True)
    return d

def db_path() -> Path:
    home = tokendog_home()
    home.mkdir(parents=True, exist_ok=True)
    return home / "cost.db"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/config.py tests/test_config.py
git commit -m "feat: tokendog config paths with TOKENDOG_HOME override"
```

---

### Task 3: Event model (`event.py`)

**Files:**
- Create: `src/tokendog/event.py`
- Test: `tests/test_event.py`

**Interfaces:**
- Produces: `TokenEvent` dataclass; constants `RUNTIME_CLAUDE="claude-code"`, `RUNTIME_GLITCH="glitch"`; `now_iso() -> str`.
- `TokenEvent` fields (order matters — the SQLite INSERT in Task 8 depends on it): `ts, session_id, runtime, event, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0, tool=None, model=None, user=None, pipeline=None, run_id=None, agent=None, cluster=None, file=None`.
- Methods: `to_json() -> str` (compact, sorted keys), `from_json(line: str) -> TokenEvent` (staticmethod).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_event.py
from tokendog.event import TokenEvent, RUNTIME_CLAUDE, RUNTIME_GLITCH, now_iso

def test_roundtrip():
    e = TokenEvent(ts="2026-07-12T00:00:00+00:00", session_id="s1",
                   runtime=RUNTIME_CLAUDE, event="PostToolUse",
                   input_tokens=10, output_tokens=5, tool="Read")
    line = e.to_json()
    back = TokenEvent.from_json(line)
    assert back == e
    assert back.runtime == "claude-code"

def test_defaults_and_glitch_fields():
    e = TokenEvent(ts="t", session_id="s", runtime=RUNTIME_GLITCH, event="context-load",
                   input_tokens=42, pipeline="feature", run_id="r1", cluster="core")
    assert e.output_tokens == 0 and e.cache_read_tokens == 0
    assert e.pipeline == "feature" and e.cluster == "core" and e.file is None

def test_now_iso_has_tz():
    assert now_iso().endswith("+00:00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_event.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.event`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/event.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json

RUNTIME_CLAUDE = "claude-code"
RUNTIME_GLITCH = "glitch"

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass
class TokenEvent:
    ts: str
    session_id: str
    runtime: str
    event: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    tool: str | None = None
    model: str | None = None
    user: str | None = None
    pipeline: str | None = None
    run_id: str | None = None
    agent: str | None = None
    cluster: str | None = None
    file: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @staticmethod
    def from_json(line: str) -> "TokenEvent":
        return TokenEvent(**json.loads(line))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_event.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/event.py tests/test_event.py
git commit -m "feat: cross-runtime TokenEvent model (claude-code + glitch fields)"
```

---

### Task 4: Token approximation (`approx.py`)

**Files:**
- Create: `src/tokendog/approx.py`
- Test: `tests/test_approx.py`

**Interfaces:**
- Produces: `approx_tokens(text: str) -> int`. Uses tiktoken `cl100k_base`; falls back to `max(1, len(text)//4)` if tiktoken import/encode fails; returns `0` for empty/None-ish input.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_approx.py
from tokendog.approx import approx_tokens

def test_empty_is_zero():
    assert approx_tokens("") == 0

def test_nonempty_positive():
    assert approx_tokens("hello world, this is a test") > 0

def test_longer_text_more_tokens():
    assert approx_tokens("word " * 100) > approx_tokens("word " * 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_approx.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.approx`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/approx.py
from __future__ import annotations

_ENC = None  # None=unloaded, False=unavailable, else encoding object

def _encoding():
    global _ENC
    if _ENC is None:
        try:
            import tiktoken
            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENC = False
    return _ENC

def approx_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _encoding()
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_approx.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/approx.py tests/test_approx.py
git commit -m "feat: tiktoken token approximation with safe fallback"
```

---

### Task 5: JSONL telemetry sink (`sink.py`)

**Files:**
- Create: `src/tokendog/sink.py`
- Test: `tests/test_sink.py`

**Interfaces:**
- Consumes: `config.telemetry_dir()`, `TokenEvent`.
- Produces: `write_event(event: TokenEvent) -> Path` (appends one JSON line to `<telemetry_dir>/<UTC-date>.jsonl`), `read_events() -> Iterator[TokenEvent]` (reads all `*.jsonl` in date order).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sink.py
from tokendog.sink import write_event, read_events
from tokendog.event import TokenEvent, RUNTIME_CLAUDE

def _ev(i):
    return TokenEvent(ts="2026-07-12T00:00:0%d+00:00" % i, session_id="s",
                      runtime=RUNTIME_CLAUDE, event="PostToolUse", input_tokens=i)

def test_write_then_read(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(_ev(1)); write_event(_ev(2))
    got = list(read_events())
    assert [e.input_tokens for e in got] == [1, 2]

def test_read_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert list(read_events()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sink.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.sink`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/sink.py
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from .config import telemetry_dir
from .event import TokenEvent

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def write_event(event: TokenEvent) -> Path:
    path = telemetry_dir() / f"{_today()}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(event.to_json() + "\n")
    return path

def read_events() -> Iterator[TokenEvent]:
    for jf in sorted(telemetry_dir().glob("*.jsonl")):
        with jf.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield TokenEvent.from_json(line)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sink.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/sink.py tests/test_sink.py
git commit -m "feat: JSONL telemetry sink (write_event/read_events)"
```

---

### Task 6: Hook entrypoint (`token_count.py`)

**Files:**
- Create: `tokendog-plugin/scripts/token_count.py`
- Test: `tests/test_token_count_hook.py`

**Interfaces:**
- Consumes: reads a Claude Code hook JSON payload from **stdin**. Relevant payload fields: `hook_event_name`, `session_id`, `tool_name`, `tool_input` (dict), `tool_output` (str or JSON, PostToolUse only), `last_assistant_message` (Stop), `model` (SessionStart).
- Produces: `main() -> int` (always returns 0). Emits one `TokenEvent` (runtime=`claude-code`) via `write_event`. Never raises.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_token_count_hook.py
import json, importlib.util, io, sys
from pathlib import Path
from tokendog.sink import read_events

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "token_count.py"

def _load():
    spec = importlib.util.spec_from_file_location("token_count", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _run(mod, payload, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return mod.main()

def test_posttooluse_records_event(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    rc = _run(mod, {"hook_event_name": "PostToolUse", "session_id": "s1",
                    "tool_name": "Read", "tool_input": {"file_path": "a.py"},
                    "tool_output": "line1\nline2\n" * 50}, monkeypatch)
    assert rc == 0
    events = list(read_events())
    assert len(events) == 1
    e = events[0]
    assert e.runtime == "claude-code" and e.event == "PostToolUse"
    assert e.tool == "Read" and e.output_tokens > 0

def test_bad_stdin_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json{{"))
    assert mod.main() == 0
    assert list(read_events()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_token_count_hook.py -q`
Expected: FAIL — file `tokendog-plugin/scripts/token_count.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# tokendog-plugin/scripts/token_count.py
"""PreToolUse/PostToolUse/Stop/SessionStart hook: approximate token spend -> JSONL sink.
Never crashes the session: any failure exits 0 and emits nothing.
Requires `pip install tokendog` (the shared package)."""
import json
import os
import sys

def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        from tokendog.event import TokenEvent, RUNTIME_CLAUDE, now_iso
        from tokendog.approx import approx_tokens
        from tokendog.sink import write_event
    except Exception:
        return 0
    try:
        input_tokens = 0
        output_tokens = 0
        if "tool_input" in payload:
            input_tokens = approx_tokens(json.dumps(payload.get("tool_input") or {}))
        out = payload.get("tool_output")
        if out is not None:
            output_tokens += approx_tokens(out if isinstance(out, str) else json.dumps(out))
        last = payload.get("last_assistant_message")
        if last:
            output_tokens += approx_tokens(last)
        event = TokenEvent(
            ts=now_iso(),
            session_id=payload.get("session_id", "unknown"),
            runtime=RUNTIME_CLAUDE,
            event=payload.get("hook_event_name", "unknown"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool=payload.get("tool_name"),
            model=payload.get("model"),
            user=os.environ.get("USER") or os.environ.get("USERNAME"),
        )
        write_event(event)
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_token_count_hook.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/scripts/token_count.py tests/test_token_count_hook.py
git commit -m "feat: token_count hook (approximate spend, crash-proof)"
```

---

### Task 7: Pricing (`pricing.py`)

**Files:**
- Create: `src/tokendog/pricing.py`
- Test: `tests/test_pricing.py`

**Interfaces:**
- Produces: `PRICES: dict[str, tuple[float, float]]` (USD per 1M tokens, `(input, output)`, includes `"default"`), `estimate_cost(input_tokens: int, output_tokens: int, model: str | None = None) -> float`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pricing.py
from tokendog.pricing import estimate_cost, PRICES

def test_uses_default_when_model_unknown():
    pin, pout = PRICES["default"]
    got = estimate_cost(1_000_000, 0, model=None)
    assert abs(got - pin) < 1e-9

def test_output_priced():
    pin, pout = PRICES["default"]
    assert abs(estimate_cost(0, 1_000_000, None) - pout) < 1e-9

def test_zero_is_zero():
    assert estimate_cost(0, 0, "anything") == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pricing.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.pricing`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/pricing.py
from __future__ import annotations

# USD per 1,000,000 tokens as (input, output).
# CONFIGURABLE DEFAULTS — verify/override against current Anthropic pricing.
PRICES: dict[str, tuple[float, float]] = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (0.80, 4.0),
    "default": (3.0, 15.0),
}

def _match(model: str | None) -> tuple[float, float]:
    if model:
        m = model.lower()
        for key, price in PRICES.items():
            if key != "default" and key in m:
                return price
    return PRICES["default"]

def estimate_cost(input_tokens: int, output_tokens: int, model: str | None = None) -> float:
    pin, pout = _match(model)
    return (input_tokens / 1_000_000) * pin + (output_tokens / 1_000_000) * pout
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pricing.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/pricing.py tests/test_pricing.py
git commit -m "feat: configurable cost estimation table"
```

---

### Task 8: SQLite backend (`backend.py`)

**Files:**
- Create: `src/tokendog/backend.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `TokenEvent`, `estimate_cost`, `config.db_path`.
- Produces:
  - `QueryFilter(group_by: str = "runtime", since: str | None = None, until: str | None = None)`
  - `RollupRow(key: str, calls: int, input_tokens: int, output_tokens: int, est_cost_usd: float)`
  - `Rollup(group_by: str, rows: list[RollupRow])`
  - `CostBackend` Protocol (`ingest(event)`, `query(filters) -> Rollup`)
  - `LocalSQLiteBackend(path=None)` — `path=":memory:"` supported. Allowed group_by keys: `runtime, user, tool, model, pipeline, day, session_id, agent, cluster` (`day` = `substr(ts,1,10)`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backend.py
import pytest
from tokendog.backend import LocalSQLiteBackend, QueryFilter
from tokendog.event import TokenEvent, RUNTIME_CLAUDE, RUNTIME_GLITCH

def _b():
    return LocalSQLiteBackend(path=":memory:")

def test_group_by_runtime():
    b = _b()
    b.ingest(TokenEvent(ts="2026-07-12T01", session_id="s", runtime=RUNTIME_CLAUDE,
                        event="PostToolUse", input_tokens=10, output_tokens=2))
    b.ingest(TokenEvent(ts="2026-07-12T02", session_id="s", runtime=RUNTIME_GLITCH,
                        event="context-load", input_tokens=100))
    rollup = b.query(QueryFilter(group_by="runtime"))
    by = {r.key: r for r in rollup.rows}
    assert by["glitch"].input_tokens == 100
    assert by["claude-code"].calls == 1
    assert by["claude-code"].est_cost_usd > 0

def test_since_filter():
    b = _b()
    b.ingest(TokenEvent(ts="2026-07-10T00", session_id="s", runtime=RUNTIME_CLAUDE, event="x", input_tokens=1))
    b.ingest(TokenEvent(ts="2026-07-12T00", session_id="s", runtime=RUNTIME_CLAUDE, event="x", input_tokens=5))
    rollup = b.query(QueryFilter(group_by="runtime", since="2026-07-11"))
    assert rollup.rows[0].input_tokens == 5

def test_invalid_group_by_rejected():
    with pytest.raises(ValueError):
        _b().query(QueryFilter(group_by="drop table"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backend.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.backend`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/backend.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
import sqlite3
from .event import TokenEvent
from .pricing import estimate_cost
from .config import db_path

_ALLOWED_GROUP = {"runtime", "user", "tool", "model", "pipeline",
                  "day", "session_id", "agent", "cluster"}

@dataclass
class QueryFilter:
    group_by: str = "runtime"
    since: str | None = None
    until: str | None = None

@dataclass
class RollupRow:
    key: str
    calls: int
    input_tokens: int
    output_tokens: int
    est_cost_usd: float

@dataclass
class Rollup:
    group_by: str
    rows: list[RollupRow]

class CostBackend(Protocol):
    def ingest(self, event: TokenEvent) -> None: ...
    def query(self, filters: QueryFilter) -> Rollup: ...

_COLUMNS = ("ts", "session_id", "runtime", "event", "input_tokens", "output_tokens",
            "cache_read_tokens", "cache_creation_tokens", "tool", "model", "user",
            "pipeline", "run_id", "agent", "cluster", "file")

class LocalSQLiteBackend:
    def __init__(self, path=None):
        self._conn = sqlite3.connect(str(path) if path is not None else str(db_path()))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events (%s)" %
            ", ".join(f"{c} {'INTEGER' if c.endswith('_tokens') else 'TEXT'}" for c in _COLUMNS)
        )
        self._conn.commit()

    def ingest(self, event: TokenEvent) -> None:
        vals = tuple(getattr(event, c) for c in _COLUMNS)
        self._conn.execute(
            "INSERT INTO events (%s) VALUES (%s)" % (", ".join(_COLUMNS), ", ".join("?" * len(_COLUMNS))),
            vals,
        )
        self._conn.commit()

    def query(self, filters: QueryFilter) -> Rollup:
        gb = filters.group_by
        if gb not in _ALLOWED_GROUP:
            raise ValueError(f"invalid group_by: {gb}")
        col = "substr(ts,1,10)" if gb == "day" else gb
        sql = (f"SELECT COALESCE({col},'(none)') AS k, COUNT(*), "
               f"COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
               f"MAX(model) FROM events")
        clauses, params = [], []
        if filters.since:
            clauses.append("ts >= ?"); params.append(filters.since)
        if filters.until:
            clauses.append("ts <= ?"); params.append(filters.until)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " GROUP BY k ORDER BY (COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0)) DESC"
        rows = []
        for k, calls, itok, otok, model in self._conn.execute(sql, params):
            rows.append(RollupRow(key=str(k), calls=calls, input_tokens=itok,
                                  output_tokens=otok,
                                  est_cost_usd=round(estimate_cost(itok, otok, model), 6)))
        return Rollup(group_by=gb, rows=rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backend.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/backend.py tests/test_backend.py
git commit -m "feat: SQLite cost backend with grouped rollups + safe group_by"
```

---

### Task 9: Ingestion — sink + Glitch (`ingest.py`)

**Files:**
- Create: `src/tokendog/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `read_events`, `TokenEvent`, a `CostBackend`-shaped object.
- Produces:
  - `ingest_sink(backend) -> int` — feed all JSONL sink events into `backend`, return count.
  - `ingest_glitch_firmware(backend, db_path) -> int` — read Glitch `context_log(session_id, file, loaded_at, tier, tokens_used)`, emit one `TokenEvent(runtime="glitch", event="context-load", input_tokens=tokens_used, tool="context:<tier>", file=file, ts=loaded_at)` per row; return count. Missing file/table -> 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest.py
import sqlite3
from tokendog.ingest import ingest_sink, ingest_glitch_firmware
from tokendog.backend import LocalSQLiteBackend, QueryFilter
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE

def test_ingest_sink(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(TokenEvent(ts="2026-07-12T00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="PostToolUse", input_tokens=7))
    b = LocalSQLiteBackend(path=":memory:")
    assert ingest_sink(b) == 1
    assert b.query(QueryFilter(group_by="runtime")).rows[0].input_tokens == 7

def _make_glitch_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE context_log (session_id TEXT, file TEXT, loaded_at TEXT, tier TEXT, tokens_used INTEGER, PRIMARY KEY (session_id, file))")
    conn.execute("INSERT INTO context_log VALUES ('g1','a.py','2026-07-12T00','hot',321)")
    conn.execute("INSERT INTO context_log VALUES ('g1','b.py','2026-07-12T01','warm',100)")
    conn.commit(); conn.close()

def test_ingest_glitch(tmp_path):
    db = tmp_path / "firmware.db"
    _make_glitch_db(db)
    b = LocalSQLiteBackend(path=":memory:")
    assert ingest_glitch_firmware(b, db) == 2
    rollup = b.query(QueryFilter(group_by="runtime"))
    assert rollup.rows[0].key == "glitch"
    assert rollup.rows[0].input_tokens == 421

def test_ingest_glitch_missing_db(tmp_path):
    b = LocalSQLiteBackend(path=":memory:")
    assert ingest_glitch_firmware(b, tmp_path / "nope.db") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.ingest`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/ingest.py
from __future__ import annotations
import sqlite3
from pathlib import Path
from .event import TokenEvent, RUNTIME_GLITCH
from .sink import read_events

def ingest_sink(backend) -> int:
    n = 0
    for ev in read_events():
        backend.ingest(ev)
        n += 1
    return n

def ingest_glitch_firmware(backend, db_path) -> int:
    path = Path(db_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path))
    n = 0
    try:
        try:
            cur = conn.execute(
                "SELECT session_id, file, loaded_at, tier, tokens_used FROM context_log")
        except sqlite3.OperationalError:
            return 0
        for session_id, file, loaded_at, tier, tokens_used in cur.fetchall():
            backend.ingest(TokenEvent(
                ts=loaded_at or "",
                session_id=session_id or "unknown",
                runtime=RUNTIME_GLITCH,
                event="context-load",
                input_tokens=int(tokens_used or 0),
                output_tokens=0,
                tool=f"context:{tier}" if tier else "context",
                file=file,
            ))
            n += 1
    finally:
        conn.close()
    return n
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ingest.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/ingest.py tests/test_ingest.py
git commit -m "feat: ingest JSONL sink + Glitch firmware context_log"
```

---

### Task 10: Reporting + CLI (`report.py`)

**Files:**
- Create: `src/tokendog/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `LocalSQLiteBackend`, `QueryFilter`, `Rollup`, `ingest_sink`, `ingest_glitch_firmware`, `config.tokendog_home`.
- Produces:
  - `cost_summary(group_by="runtime", since=None, until=None, glitch_db=None) -> dict` — builds a **fresh in-memory** backend, ingests sink (+ glitch if `glitch_db` given), returns `{"group_by": str, "rows": [ {key, calls, input_tokens, output_tokens, est_cost_usd}, ... ]}`.
  - `format_rollup(summary: dict) -> str` — markdown table (columns: Group, Calls, In, Out, Est \$). Ends with a note that numbers are approximations.
  - `doctor_report(cwd: str) -> str` — multi-line env check: TOKENDOG_HOME path, telemetry event count, tiktoken availability, Glitch DB presence.
  - `main(argv=None) -> int` — CLI: `tokendog cost [--group-by G] [--since S] [--until U] [--glitch-db PATH]` prints `format_rollup`; `tokendog doctor` prints `doctor_report(cwd)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
from tokendog import report
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE

def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(TokenEvent(ts="2026-07-12T00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="PostToolUse", tool="Read", input_tokens=10, output_tokens=4))

def test_cost_summary_shape(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    s = report.cost_summary(group_by="runtime")
    assert s["group_by"] == "runtime"
    assert s["rows"][0]["key"] == "claude-code"
    assert s["rows"][0]["input_tokens"] == 10

def test_format_rollup_is_markdown(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = report.format_rollup(report.cost_summary())
    assert "| Group" in out and "claude-code" in out and "approxim" in out.lower()

def test_doctor_mentions_home(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    txt = report.doctor_report(str(tmp_path))
    assert str(tmp_path) in txt and "tiktoken" in txt.lower()

def test_cli_cost(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    rc = report.main(["cost", "--group-by", "tool"])
    assert rc == 0
    assert "Read" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_report.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.report`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/report.py
from __future__ import annotations
import argparse
import os
from .backend import LocalSQLiteBackend, QueryFilter
from .ingest import ingest_sink, ingest_glitch_firmware
from .sink import read_events
from .approx import _encoding
from .config import tokendog_home

def cost_summary(group_by="runtime", since=None, until=None, glitch_db=None) -> dict:
    backend = LocalSQLiteBackend(path=":memory:")
    ingest_sink(backend)
    if glitch_db:
        ingest_glitch_firmware(backend, glitch_db)
    rollup = backend.query(QueryFilter(group_by=group_by, since=since, until=until))
    return {"group_by": rollup.group_by,
            "rows": [r.__dict__ for r in rollup.rows]}

def format_rollup(summary: dict) -> str:
    lines = [f"### TokenDog cost by {summary['group_by']}",
             "",
             "| Group | Calls | In | Out | Est $ |",
             "|---|--:|--:|--:|--:|"]
    for r in summary["rows"]:
        lines.append("| {key} | {calls} | {input_tokens} | {output_tokens} | ${est_cost_usd:.4f} |".format(**r))
    if len(lines) == 4:
        lines.append("| _(no data yet)_ | | | | |")
    lines.append("")
    lines.append("_Numbers are local tiktoken approximations, not authoritative billing._")
    return "\n".join(lines)

def doctor_report(cwd: str) -> str:
    home = tokendog_home()
    n_events = sum(1 for _ in read_events())
    tik = "available" if _encoding() else "MISSING (falling back to len/4) — run `pip install tiktoken`"
    glitch = os.path.join(cwd, ".glitch", "firmware", "firmware.db")
    glitch_status = "present" if os.path.exists(glitch) else "not found"
    return "\n".join([
        "TokenDog doctor",
        f"- state dir: {home}",
        f"- telemetry events recorded: {n_events}",
        f"- tiktoken: {tik}",
        f"- glitch firmware.db: {glitch_status} ({glitch})",
    ])

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tokendog")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cost")
    c.add_argument("--group-by", default="runtime")
    c.add_argument("--since"); c.add_argument("--until"); c.add_argument("--glitch-db")
    sub.add_parser("doctor")
    args = p.parse_args(argv)
    if args.cmd == "cost":
        print(format_rollup(cost_summary(args.group_by, args.since, args.until, args.glitch_db)))
    elif args.cmd == "doctor":
        print(doctor_report(os.getcwd()))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/report.py tests/test_report.py
git commit -m "feat: cost report, markdown formatter, doctor, CLI"
```

---

### Task 11: MCP server + plugin manifest + hooks wiring

**Files:**
- Create: `tokendog-plugin/mcpServers/tokendog-cost/server.py`
- Create: `tokendog-plugin/.claude-plugin/plugin.json`
- Create: `tokendog-plugin/hooks/hooks.json`
- Test: `tests/test_plugin_wiring.py`

**Interfaces:**
- Consumes: `tokendog.report.cost_summary`, `doctor_report`.
- Produces: an MCP stdio server exposing tools `cost_summary(group_by, since, until)` and `doctor()`. Plugin manifest registering hooks + the MCP server. `hooks.json` running `token_count.py` on PreToolUse/PostToolUse/Stop/SessionStart.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plugin_wiring.py
import json, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tokendog-plugin"

def test_plugin_manifest_valid():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert m["name"] == "tokendog"
    assert m["hooks"] == "./hooks/hooks.json"
    assert "tokendog-cost" in m["mcpServers"]

def test_hooks_json_registers_token_count():
    h = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    events = h["hooks"]
    for ev in ("PreToolUse", "PostToolUse", "Stop", "SessionStart"):
        assert ev in events
        cmd = events[ev][0]["hooks"][0]["command"]
        assert "token_count.py" in cmd

def test_server_module_imports_and_exposes_tools():
    server_path = ROOT / "mcpServers" / "tokendog-cost" / "server.py"
    spec = importlib.util.spec_from_file_location("tokendog_cost_server", server_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "mcp")
    assert callable(mod.cost_summary)
    assert callable(mod.doctor)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plugin_wiring.py -q`
Expected: FAIL — files do not exist.

- [ ] **Step 3: Write the implementation files**

`tokendog-plugin/.claude-plugin/plugin.json`:
```json
{
  "name": "tokendog",
  "displayName": "TokenDog",
  "version": "0.1.0",
  "description": "Watch and reduce Claude Code / Glitch token spend.",
  "license": "Apache-2.0",
  "hooks": "./hooks/hooks.json",
  "commands": ["./commands/tokendog-cost.md", "./commands/tokendog-doctor.md"],
  "mcpServers": {
    "tokendog-cost": {
      "command": "python3",
      "args": ["${CLAUDE_PLUGIN_ROOT}/mcpServers/tokendog-cost/server.py"],
      "cwd": "${CLAUDE_PROJECT_DIR}"
    }
  }
}
```

`tokendog-plugin/hooks/hooks.json`:
```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/token_count.py", "timeout": 10 } ] }
    ],
    "PostToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/token_count.py", "timeout": 10 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/token_count.py", "timeout": 10 } ] }
    ],
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/token_count.py", "timeout": 10 } ] }
    ]
  }
}
```

`tokendog-plugin/mcpServers/tokendog-cost/server.py`:
```python
#!/usr/bin/env python3
"""tokendog-cost MCP server: expose token-spend rollups to Claude Code.
Requires `pip install tokendog`. Reads the JSONL sink + Glitch firmware.db (cwd)."""
import os
import sys

try:
    from mcp.server.mcpserver import MCPServer as _Server
except Exception:  # older SDKs
    from mcp.server.fastmcp import FastMCP as _Server

from tokendog import report

mcp = _Server("tokendog-cost")

def _glitch_db() -> str | None:
    path = os.path.join(os.getcwd(), ".glitch", "firmware", "firmware.db")
    return path if os.path.exists(path) else None

@mcp.tool()
def cost_summary(group_by: str = "runtime", since: str | None = None,
                 until: str | None = None) -> dict:
    """Token-spend rollup grouped by runtime|user|tool|model|pipeline|day|session_id|agent|cluster."""
    return report.cost_summary(group_by=group_by, since=since, until=until,
                               glitch_db=_glitch_db())

@mcp.tool()
def doctor() -> str:
    """Environment check: state dir, event count, tiktoken availability, Glitch DB presence."""
    return report.doctor_report(os.getcwd())

if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_plugin_wiring.py -q`
Expected: PASS (3 passed). (Requires `pip install -e .` so `tokendog` and `mcp` import.)

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/.claude-plugin/plugin.json tokendog-plugin/hooks/hooks.json tokendog-plugin/mcpServers/tokendog-cost/server.py tests/test_plugin_wiring.py
git commit -m "feat: tokendog-cost MCP server + plugin manifest + hooks wiring"
```

---

### Task 12: Slash commands + end-to-end smoke

**Files:**
- Create: `tokendog-plugin/commands/tokendog-cost.md`
- Create: `tokendog-plugin/commands/tokendog-doctor.md`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: the `tokendog` CLI (`python -m tokendog.report`) for deterministic dynamic-context injection.
- Produces: two user-invocable slash commands.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_commands.py
from pathlib import Path
CMD = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "commands"

def test_cost_command_frontmatter_and_body():
    txt = (CMD / "tokendog-cost.md").read_text()
    assert txt.startswith("---")
    assert "description:" in txt
    assert "tokendog.report" in txt  # injects real rollup

def test_doctor_command_present():
    txt = (CMD / "tokendog-doctor.md").read_text()
    assert "doctor" in txt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_commands.py -q`
Expected: FAIL — command files do not exist.

- [ ] **Step 3: Write the command files**

`tokendog-plugin/commands/tokendog-cost.md`:
```markdown
---
description: Show a TokenDog token-spend rollup (Claude Code + Glitch) for this environment.
argument-hint: "[group-by: runtime|user|tool|model|day]"
allowed-tools: Bash
---

# TokenDog — cost

Here is the current token-spend rollup (grouped by `$ARGUMENTS`, default `runtime`):

!`python -m tokendog.report cost --group-by "${ARGUMENTS:-runtime}" --glitch-db "${CLAUDE_PROJECT_DIR}/.glitch/firmware/firmware.db"`

Present the table above to the user. Note that figures are **local approximations**
(tiktoken), not authoritative billing — authoritative numbers arrive once the
TokenDog gate is deployed. If the table is empty, tell the user to run a few tool
calls first so telemetry can accumulate.
```

`tokendog-plugin/commands/tokendog-doctor.md`:
```markdown
---
description: TokenDog environment check — telemetry sink, tiktoken, Glitch DB.
allowed-tools: Bash
---

# TokenDog — doctor

!`python -m tokendog.report doctor`

Review the report above. If tiktoken is MISSING, tell the user to `pip install tiktoken`
for accurate approximations. If the event count is 0, telemetry hasn't recorded yet —
confirm the plugin is installed and the hooks are firing.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_commands.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Full suite + manual end-to-end smoke**

Run: `pytest -q`
Expected: all tests pass.

Manual smoke (documented for the implementer, not automated):
1. `pip install -e ".[dev]"`
2. Install plugin: `/plugin install ./tokendog-plugin` (local path) in Claude Code.
3. Run any Read/Grep/Bash tool calls in a session.
4. Confirm `~/.tokendog/telemetry/<date>.jsonl` contains events.
5. Run `/tokendog:cost` → see a table with a `claude-code` row (and `glitch` if a Glitch run has populated `context_log`).
6. Run `/tokendog:doctor` → all checks green.

- [ ] **Step 6: Commit**

```bash
git add tokendog-plugin/commands tests/test_commands.py
git commit -m "feat: /tokendog:cost and /tokendog:doctor slash commands"
```

---

## Exit criterion (Slice 1 done)

Run a real Claude Code session for a few minutes with the plugin installed, then `/tokendog:cost` shows your own approximate spend broken down by runtime/tool, and `/tokendog:doctor` is green. Glitch spend appears automatically once a Glitch pipeline has written to `context_log`. Telemetry schema already carries `runtime/pipeline/run_id/agent/cluster`, so later slices (frugal skill, budgets, gate) build on this without migration.

## Self-review notes

- **Spec coverage:** brief items 32 (telemetry hook), 34 (cost MCP + pluggable backend Protocol), 35 (`/tokendog:cost`), plus doctor and Glitch ingestion (spec §7) — all have tasks. Per-user/team rollup: `user` grouping is supported; "team" mapping is deferred to Slice 2 config (noted, not silently dropped).
- **Placeholders:** none — every step has runnable code/commands.
- **Type consistency:** `TokenEvent` field order (Task 3) matches `_COLUMNS` (Task 8); `cost_summary` dict shape (Task 10) matches what `format_rollup` and the MCP tool consume (Tasks 10–11); `QueryFilter`/`Rollup`/`RollupRow` names consistent across Tasks 8–11.
- **Deferred (honest):** authoritative `usage.*` (item 33) and cache-token fields stay 0 until the gate (Slice 6); team attribution to Slice 2/3 templates.
