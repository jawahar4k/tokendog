# TokenDog Slice 4 — MCP Author Toolkit (Python) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship a small, framework-agnostic Python library (`tokendog_mcp`) that MCP-server authors drop in to make their MCPs frugal — deferred tool loading, response pagination, response truncation, batch-query dedup, and dense tool-schema helpers — with pattern docs.

**Architecture:** A standalone package `tokendog_mcp` under `tokendog-mcp-toolkit/py/` (its own `pyproject.toml` for distribution). Each concern is a small pure module with no framework dependency — authors wire them into whatever MCP SDK they use. Tests live in the existing `tests/` dir; the repo's pytest `pythonpath` is extended so `import tokendog_mcp` works without a separate install. No third-party dependencies.

**Tech Stack:** Python 3.11+, stdlib only, `pytest`.

## Global Constraints

- Prefix `tokendog` / package `tokendog_mcp`; Apache-2.0; Python 3.11+; **no third-party deps**.
- Commands run in the Slice 1 `.venv` (`.venv/bin/python -m pytest …`).
- The toolkit is **framework-agnostic** — no import of `mcp`, no assumption about a specific server. Pure helpers over plain data + callables.
- This is a SEPARATE distributable from the `tokendog` package; light duplication of a truncation idea is acceptable (different audience). Do not import `tokendog` from `tokendog_mcp`.
- TDD; commit after each green task. Full suite (Slices 1–4) stays green.

---

### Task 1: Toolkit scaffold + pytest path

**Files:**
- Create: `tokendog-mcp-toolkit/py/tokendog_mcp/__init__.py`
- Create: `tokendog-mcp-toolkit/py/pyproject.toml`
- Modify: `pyproject.toml` (repo root — extend pytest `pythonpath`)
- Test: `tests/test_toolkit_import.py`

**Interfaces:**
- Produces: importable package `tokendog_mcp` (version marker), discoverable by the repo's pytest.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_toolkit_import.py
def test_toolkit_importable():
    import tokendog_mcp
    assert hasattr(tokendog_mcp, "__version__")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_toolkit_import.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog_mcp`.

- [ ] **Step 3: Create the package + extend pytest path**

`tokendog-mcp-toolkit/py/tokendog_mcp/__init__.py`:
```python
"""tokendog_mcp — frugal building blocks for MCP server authors."""
__version__ = "0.1.0"
```

`tokendog-mcp-toolkit/py/pyproject.toml`:
```toml
[project]
name = "tokendog-mcp-toolkit"
version = "0.1.0"
description = "Frugal building blocks for MCP server authors (deferred loading, pagination, batching, dense schemas)."
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
dependencies = []

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
```

In the repo-root `pyproject.toml`, extend the pytest path (change the existing `pythonpath` line):
```toml
[tool.pytest.ini_options]
pythonpath = ["src", "tokendog-mcp-toolkit/py"]
testpaths = ["tests"]
```

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_toolkit_import.py -q && .venv/bin/python -m pytest -q`
Expected: import test passes; full suite still green.

- [ ] **Step 5: Commit**

```bash
git add tokendog-mcp-toolkit/py/tokendog_mcp/__init__.py tokendog-mcp-toolkit/py/pyproject.toml pyproject.toml tests/test_toolkit_import.py
git commit -m "feat: tokendog-mcp-toolkit scaffold + pytest path"
```

---

### Task 2: Pagination (`pagination.py`)

**Files:**
- Create: `tokendog-mcp-toolkit/py/tokendog_mcp/pagination.py`
- Test: `tests/test_toolkit_pagination.py`

**Interfaces:**
- Produces: `paginate(items, page: int = 1, page_size: int = 50) -> dict` → `{"items", "page", "page_size", "total", "has_more", "next_page"}`. Clamps `page`/`page_size` to ≥1.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_toolkit_pagination.py
from tokendog_mcp.pagination import paginate

def test_first_page_has_more():
    r = paginate(list(range(120)), page=1, page_size=50)
    assert r["items"] == list(range(50))
    assert r["total"] == 120 and r["has_more"] is True and r["next_page"] == 2

def test_last_page_no_more():
    r = paginate(list(range(120)), page=3, page_size=50)
    assert r["items"] == list(range(100, 120))
    assert r["has_more"] is False and r["next_page"] is None

def test_clamps_bad_args():
    r = paginate([1, 2, 3], page=0, page_size=0)
    assert r["page"] == 1 and r["page_size"] == 1 and r["items"] == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_toolkit_pagination.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# tokendog-mcp-toolkit/py/tokendog_mcp/pagination.py
from __future__ import annotations

def paginate(items, page: int = 1, page_size: int = 50) -> dict:
    items = list(items)
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    total = len(items)
    start = (page - 1) * page_size
    chunk = items[start:start + page_size]
    has_more = start + page_size < total
    return {
        "items": chunk,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": has_more,
        "next_page": page + 1 if has_more else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_toolkit_pagination.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-mcp-toolkit/py/tokendog_mcp/pagination.py tests/test_toolkit_pagination.py
git commit -m "feat: mcp-toolkit pagination helper"
```

---

### Task 3: Response truncation (`truncate.py`)

**Files:**
- Create: `tokendog-mcp-toolkit/py/tokendog_mcp/truncate.py`
- Test: `tests/test_toolkit_truncate.py`

**Interfaces:**
- Produces: `cap_items(items, max_items: int = 100) -> tuple[list, bool]`; `cap_text(text: str, max_bytes: int = 50_000) -> tuple[str, bool]`. Each returns `(value, was_truncated)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_toolkit_truncate.py
from tokendog_mcp.truncate import cap_items, cap_text

def test_cap_items():
    got, cut = cap_items(list(range(500)), max_items=100)
    assert len(got) == 100 and cut is True
    got2, cut2 = cap_items([1, 2], max_items=100)
    assert got2 == [1, 2] and cut2 is False

def test_cap_text():
    out, cut = cap_text("x" * 1000, max_bytes=100)
    assert len(out.encode("utf-8")) <= 100 and cut is True
    out2, cut2 = cap_text("short", max_bytes=100)
    assert out2 == "short" and cut2 is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_toolkit_truncate.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# tokendog-mcp-toolkit/py/tokendog_mcp/truncate.py
from __future__ import annotations

def cap_items(items, max_items: int = 100) -> tuple[list, bool]:
    items = list(items)
    if len(items) <= max_items:
        return items, False
    return items[:max_items], True

def cap_text(text: str, max_bytes: int = 50_000) -> tuple[str, bool]:
    if not text:
        return "", False
    b = text.encode("utf-8")
    if len(b) <= max_bytes:
        return text, False
    return b[:max_bytes].decode("utf-8", "ignore"), True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_toolkit_truncate.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-mcp-toolkit/py/tokendog_mcp/truncate.py tests/test_toolkit_truncate.py
git commit -m "feat: mcp-toolkit response-truncation helpers"
```

---

### Task 4: Batch-query dedup (`batch.py`)

**Files:**
- Create: `tokendog-mcp-toolkit/py/tokendog_mcp/batch.py`
- Test: `tests/test_toolkit_batch.py`

**Interfaces:**
- Produces: `run_batch(keys, fetch_one) -> dict` — dedupes `keys` (order-preserving), calls `fetch_one(key)` once per unique key, returns `{key: result}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_toolkit_batch.py
from tokendog_mcp.batch import run_batch

def test_dedupes_calls():
    calls = []
    def fetch(k):
        calls.append(k)
        return k * 2
    result = run_batch(["a", "b", "a", "b", "b"], fetch)
    assert result == {"a": "aa", "b": "bb"}
    assert calls == ["a", "b"]  # each unique key fetched exactly once

def test_empty():
    assert run_batch([], lambda k: k) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_toolkit_batch.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# tokendog-mcp-toolkit/py/tokendog_mcp/batch.py
from __future__ import annotations

def run_batch(keys, fetch_one) -> dict:
    """Call fetch_one once per UNIQUE key (order-preserving); return {key: result}."""
    results = {}
    for key in dict.fromkeys(keys):
        results[key] = fetch_one(key)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_toolkit_batch.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-mcp-toolkit/py/tokendog_mcp/batch.py tests/test_toolkit_batch.py
git commit -m "feat: mcp-toolkit batch-query dedup helper"
```

---

### Task 5: Dense schema helper (`schema.py`)

**Files:**
- Create: `tokendog-mcp-toolkit/py/tokendog_mcp/schema.py`
- Test: `tests/test_toolkit_schema.py`

**Interfaces:**
- Produces: `dense_tool_schema(name: str, description: str, params: dict) -> dict` where `params` maps `param_name -> (json_type, required: bool, description: str)`. Returns `{"name", "description", "inputSchema": {"type": "object", "properties": {...}, "required": [...]}}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_toolkit_schema.py
from tokendog_mcp.schema import dense_tool_schema

def test_builds_schema():
    s = dense_tool_schema("get_issue", "Fetch one issue",
                          {"id": ("string", True, "issue id"),
                           "fields": ("array", False, "fields to return")})
    assert s["name"] == "get_issue"
    props = s["inputSchema"]["properties"]
    assert props["id"]["type"] == "string"
    assert s["inputSchema"]["required"] == ["id"]
    assert "fields" not in s["inputSchema"]["required"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_toolkit_schema.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# tokendog-mcp-toolkit/py/tokendog_mcp/schema.py
from __future__ import annotations

def dense_tool_schema(name: str, description: str, params: dict) -> dict:
    """params: {param_name: (json_type, required_bool, description)}."""
    properties = {}
    required = []
    for pname, (ptype, req, desc) in params.items():
        properties[pname] = {"type": ptype, "description": desc}
        if req:
            required.append(pname)
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties, "required": required},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_toolkit_schema.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-mcp-toolkit/py/tokendog_mcp/schema.py tests/test_toolkit_schema.py
git commit -m "feat: mcp-toolkit dense tool-schema helper"
```

---

### Task 6: Deferred tool registry (`deferred.py`)

**Files:**
- Create: `tokendog-mcp-toolkit/py/tokendog_mcp/deferred.py`
- Test: `tests/test_toolkit_deferred.py`

**Interfaces:**
- Produces: `DeferredRegistry` with `.tool(name, schema=None, deferred=False)` decorator, `.eager_tools() -> list[str]`, `.deferred_tools() -> list[str]`, `.load(name) -> schema` (on-demand schema fetch), `.call(name, *a, **k)` (invoke handler).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_toolkit_deferred.py
from tokendog_mcp.deferred import DeferredRegistry

def test_partitions_and_loads():
    reg = DeferredRegistry()

    @reg.tool("common", schema={"x": 1})
    def common(): return "c"

    @reg.tool("rare", schema={"y": 2}, deferred=True)
    def rare(): return "r"

    assert reg.eager_tools() == ["common"]
    assert reg.deferred_tools() == ["rare"]
    assert reg.load("rare") == {"y": 2}   # schema fetched on demand
    assert reg.call("rare") == "r"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_toolkit_deferred.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# tokendog-mcp-toolkit/py/tokendog_mcp/deferred.py
from __future__ import annotations

class DeferredRegistry:
    """Register MCP tools; mark rarely-used ones `deferred` so their schemas
    load on demand instead of inflating the fixed prompt on every call."""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    def tool(self, name: str, schema=None, deferred: bool = False):
        def deco(fn):
            self._tools[name] = {"schema": schema, "deferred": deferred, "handler": fn}
            return fn
        return deco

    def eager_tools(self) -> list[str]:
        return [n for n, t in self._tools.items() if not t["deferred"]]

    def deferred_tools(self) -> list[str]:
        return [n for n, t in self._tools.items() if t["deferred"]]

    def load(self, name: str):
        return self._tools[name]["schema"]

    def call(self, name: str, *args, **kwargs):
        return self._tools[name]["handler"](*args, **kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_toolkit_deferred.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-mcp-toolkit/py/tokendog_mcp/deferred.py tests/test_toolkit_deferred.py
git commit -m "feat: mcp-toolkit deferred tool registry"
```

---

### Task 7: Public exports + pattern docs + full suite

**Files:**
- Modify: `tokendog-mcp-toolkit/py/tokendog_mcp/__init__.py` (re-export the public API)
- Create: `tokendog-mcp-toolkit/docs/deferred-loading.md`
- Create: `tokendog-mcp-toolkit/docs/pagination.md`
- Create: `tokendog-mcp-toolkit/docs/batch-queries.md`
- Create: `tokendog-mcp-toolkit/docs/structured-schemas.md`
- Test: `tests/test_toolkit_exports.py`, `tests/test_toolkit_docs.py`

**Interfaces:**
- Produces: top-level imports (`from tokendog_mcp import paginate, cap_items, cap_text, run_batch, dense_tool_schema, DeferredRegistry`) and four one-page pattern guides.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_toolkit_exports.py
def test_public_api():
    from tokendog_mcp import (paginate, cap_items, cap_text, run_batch,
                              dense_tool_schema, DeferredRegistry)
    assert callable(paginate) and callable(run_batch) and callable(dense_tool_schema)
    assert isinstance(DeferredRegistry(), DeferredRegistry)
```

```python
# tests/test_toolkit_docs.py
from pathlib import Path
D = Path(__file__).resolve().parents[1] / "tokendog-mcp-toolkit" / "docs"

def test_docs_exist():
    for name in ("deferred-loading", "pagination", "batch-queries", "structured-schemas"):
        p = D / f"{name}.md"
        assert p.exists() and len(p.read_text()) > 100, name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_toolkit_exports.py tests/test_toolkit_docs.py -q`
Expected: FAIL — exports/docs missing.

- [ ] **Step 3a: Re-export the public API**

Replace `tokendog-mcp-toolkit/py/tokendog_mcp/__init__.py`:
```python
"""tokendog_mcp — frugal building blocks for MCP server authors."""
__version__ = "0.1.0"

from .pagination import paginate
from .truncate import cap_items, cap_text
from .batch import run_batch
from .schema import dense_tool_schema
from .deferred import DeferredRegistry

__all__ = ["paginate", "cap_items", "cap_text", "run_batch",
           "dense_tool_schema", "DeferredRegistry"]
```

- [ ] **Step 3b: Write the four pattern docs**

`tokendog-mcp-toolkit/docs/deferred-loading.md`:
```markdown
# Deferred tool loading

MCP tool schemas are part of the fixed prompt — paid on every call. Mark rarely-used tools
`deferred` so their schemas load on demand instead of inflating every request.

```python
from tokendog_mcp import DeferredRegistry
reg = DeferredRegistry()

@reg.tool("search", schema=SEARCH_SCHEMA)            # common → eager
def search(q): ...

@reg.tool("export_pdf", schema=PDF_SCHEMA, deferred=True)   # rare → deferred
def export_pdf(id): ...

reg.eager_tools()      # ["search"]  → advertised up front
reg.deferred_tools()   # ["export_pdf"] → schema fetched via reg.load(name) when needed
```
Advertise only `eager_tools()` in your server's tool list; expose a lookup that calls
`reg.load(name)` when a client asks for a deferred tool.
```

`tokendog-mcp-toolkit/docs/pagination.md`:
```markdown
# Pagination

Never return an unbounded list from an MCP tool — page it.

```python
from tokendog_mcp import paginate
page = paginate(all_rows, page=1, page_size=50)
# {"items": [...50...], "total": N, "has_more": bool, "next_page": 2 or None}
```
Return `next_page` to the client so it can fetch more only if it needs to.
```

`tokendog-mcp-toolkit/docs/batch-queries.md`:
```markdown
# Batch queries

Expose one batched tool instead of N single-item tools; dedupe repeated keys so you fetch each
unique item once.

```python
from tokendog_mcp import run_batch
issues = run_batch(ids, fetch_one=get_issue)   # get_issue called once per unique id
```
```

`tokendog-mcp-toolkit/docs/structured-schemas.md`:
```markdown
# Dense structured schemas

Keep tool descriptions terse and structured — verbose prose in schemas is paid on every call.

```python
from tokendog_mcp import dense_tool_schema
schema = dense_tool_schema("get_issue", "Fetch one issue",
    {"id": ("string", True, "issue id"), "fields": ("array", False, "fields to return")})
```
```

- [ ] **Step 4: Run the tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_toolkit_exports.py tests/test_toolkit_docs.py -q && .venv/bin/python -m pytest -q`
Expected: exports + docs pass; full suite (Slices 1–4) green.

- [ ] **Step 5: Commit**

```bash
git add tokendog-mcp-toolkit/py/tokendog_mcp/__init__.py tokendog-mcp-toolkit/docs tests/test_toolkit_exports.py tests/test_toolkit_docs.py
git commit -m "feat: mcp-toolkit public exports + pattern docs"
```

---

## Exit criterion (Slice 4 done)

An MCP author can `from tokendog_mcp import paginate, cap_items, cap_text, run_batch, dense_tool_schema, DeferredRegistry` and use each to make their server frugal, guided by four pattern docs. Full suite green.

## Self-review notes

- **Spec coverage (spec §5 Slice 4):** deferred loading (Task 6, item 5) · pagination (Task 2, item 9) · truncation (Task 3) · batch queries (Task 4, item 15) · dense schemas (Task 5, item 7) · docs (Task 7). TS mirror explicitly deferred (spec says "later").
- **Placeholders:** none.
- **Type consistency:** helper names identical between their defining task, the `__init__` re-export (Task 7), and the export test; `DeferredRegistry` API (`tool`/`eager_tools`/`deferred_tools`/`load`/`call`) consistent between Task 6 and its doc.
- **Isolation:** `tokendog_mcp` imports nothing from `tokendog` and no third-party packages — verified by the import test running under the extended pytest path.
