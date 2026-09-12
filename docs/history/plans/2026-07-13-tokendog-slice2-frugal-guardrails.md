# TokenDog Slice 2 — Frugal Behavior + Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the behavior-shaping and guardrail layer on top of Slice 1's measurement spine — an always-on frugal skill, a session-hygiene skill, output-truncation + budget-enforcement + budget-alert + session-summary hooks, a Glitch-native `stop` telemetry hook (authoritative token counts), and `/tokendog:budget` + `/tokendog:audit` commands.

**Architecture:** Two new reusable package modules (`budget.py`, `truncate.py`) hold all logic; the plugin's hook scripts and commands are thin wrappers over them, exactly as in Slice 1. Guardrail hooks use Claude Code's documented hook outputs: PostToolUse `updatedToolOutput` (truncation), PreToolUse `permissionDecision:"deny"` (budget block), Stop `systemMessage` (summary/alert). The Glitch `stop` hook reads Glitch's env-var contract for **authoritative** token counts (no approximation), writing to the same JSONL sink as `runtime="glitch"`.

**Tech Stack:** Python 3.11+, stdlib only for new modules (`json`, `urllib.request` for webhook), `pytest`. Reuses Slice 1's `tokendog` package. No new dependencies.

## Global Constraints

- Product/identifier prefix is **`tokendog`** everywhere; state dir `~/.tokendog/` (override `TOKENDOG_HOME`).
- License Apache-2.0; Python **3.11+**.
- **No new dependencies** — stdlib only (webhook via `urllib.request`). Keeps the `tiktoken`+`mcp` footprint.
- All commands run inside the project **`.venv`** from Slice 1 (`.venv/bin/python -m pytest …`).
- Every `TokenEvent` sets `runtime` (`"claude-code"` or `"glitch"`).
- Hook scripts MUST NEVER crash/block the host session: on any error exit 0 and emit nothing (except intentional PreToolUse deny). Truncation/alert failures degrade to pass-through.
- Glitch `stop` hook produces **authoritative** counts from env vars — never tiktoken-approximated.
- Budget prices come from Slice 1's `pricing.py` (configurable). Budgets themselves live in `~/.tokendog/budget.json`.
- TDD; commit after each green task. Build on Slice 1 modules — do not duplicate `sink`/`report`/`backend` logic.

**Scope note (honest deferral):** This slice delivers the high-value guardrails. Deferred to a later slice (documented, not dropped): `mcp_response_cache` (item 16), `mcp_hygiene_scan` (40), `skills_audit` (1,2,4), `idle_cleanup` (45), `/tokendog:warmup`. They're speculative or lower-ROI than the budget/truncation/frugal-skill core and are cleaner to add once org-config templates (Slice 3) exist.

---

### Task 1: Budget module (`budget.py`)

**Files:**
- Create: `src/tokendog/budget.py`
- Test: `tests/test_budget.py`

**Interfaces:**
- Consumes: `config.tokendog_home`, `report.cost_summary`.
- Produces:
  - `Budget(daily_usd: float|None=None, session_usd: float|None=None, alert_usd: float|None=None, webhook_url: str|None=None)` dataclass.
  - `budget_path() -> Path` (`~/.tokendog/budget.json`).
  - `load_budget() -> Budget` (missing/corrupt → defaults).
  - `save_budget(b: Budget) -> Path`.
  - `spend_today(glitch_db=None) -> float`, `spend_session(session_id, glitch_db=None) -> float`.
  - `check(session_id=None, glitch_db=None) -> dict` → `{"daily": float, "session": float, "over_daily": bool, "over_session": bool, "over_alert": bool, "budget": Budget}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_budget.py
from tokendog import budget
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE
from datetime import datetime, timezone

def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _seed(tmp_path, monkeypatch, itok, otok, model="sonnet", session="s"):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(TokenEvent(ts=_today()+"T00:00:00+00:00", session_id=session,
                           runtime=RUNTIME_CLAUDE, event="PostToolUse",
                           input_tokens=itok, output_tokens=otok, model=model))

def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    budget.save_budget(budget.Budget(daily_usd=5.0, alert_usd=2.0, webhook_url="http://x"))
    b = budget.load_budget()
    assert b.daily_usd == 5.0 and b.alert_usd == 2.0 and b.webhook_url == "http://x"

def test_load_missing_is_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert budget.load_budget() == budget.Budget()

def test_spend_and_check_over_daily(tmp_path, monkeypatch):
    # 1,000,000 input tokens @ sonnet ($3/M) = $3.00
    _seed(tmp_path, monkeypatch, 1_000_000, 0)
    budget.save_budget(budget.Budget(daily_usd=1.0, alert_usd=2.0))
    result = budget.check(session_id="s")
    assert result["daily"] >= 3.0
    assert result["over_daily"] is True
    assert result["over_alert"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_budget.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.budget`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/budget.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from .config import tokendog_home
from .report import cost_summary

_FIELDS = ("daily_usd", "session_usd", "alert_usd", "webhook_url")

@dataclass
class Budget:
    daily_usd: float | None = None
    session_usd: float | None = None
    alert_usd: float | None = None
    webhook_url: str | None = None

def budget_path() -> Path:
    return tokendog_home() / "budget.json"

def load_budget() -> Budget:
    p = budget_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return Budget(**{k: data.get(k) for k in _FIELDS})
        except Exception:
            pass
    return Budget()

def save_budget(b: Budget) -> Path:
    p = budget_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(b)), encoding="utf-8")
    return p

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def spend_today(glitch_db=None) -> float:
    s = cost_summary(group_by="runtime", since=_today(), glitch_db=glitch_db)
    return round(sum(r["est_cost_usd"] for r in s["rows"]), 6)

def spend_session(session_id, glitch_db=None) -> float:
    if not session_id:
        return 0.0
    s = cost_summary(group_by="session_id", glitch_db=glitch_db)
    return round(sum(r["est_cost_usd"] for r in s["rows"] if r["key"] == session_id), 6)

def check(session_id=None, glitch_db=None) -> dict:
    b = load_budget()
    day = spend_today(glitch_db)
    sess = spend_session(session_id, glitch_db)
    return {
        "daily": day,
        "session": sess,
        "over_daily": b.daily_usd is not None and day >= b.daily_usd,
        "over_session": b.session_usd is not None and sess >= b.session_usd,
        "over_alert": b.alert_usd is not None and day >= b.alert_usd,
        "budget": b,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_budget.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/budget.py tests/test_budget.py
git commit -m "feat: budget config + spend computation + threshold checks"
```

---

### Task 2: Truncation module (`truncate.py`)

**Files:**
- Create: `src/tokendog/truncate.py`
- Test: `tests/test_truncate.py`

**Interfaces:**
- Produces: `truncate_text(text: str, max_lines: int = 200, max_bytes: int = 50_000) -> tuple[str, bool]` — returns `(possibly_truncated_text, was_truncated)`. Byte-cap first, then line-cap with a head/tail keep and a middle elision marker. `None`/empty → `("", False)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_truncate.py
from tokendog.truncate import truncate_text

def test_short_text_untouched():
    out, cut = truncate_text("a\nb\nc", max_lines=200, max_bytes=1000)
    assert out == "a\nb\nc" and cut is False

def test_line_cap_keeps_head_and_tail():
    text = "\n".join(str(i) for i in range(1000))
    out, cut = truncate_text(text, max_lines=100, max_bytes=10_000_000)
    assert cut is True
    assert "0" in out.splitlines()[0]
    assert "999" in out.splitlines()[-1]
    assert "truncated" in out

def test_byte_cap():
    out, cut = truncate_text("x" * 100_000, max_lines=100000, max_bytes=1000)
    assert cut is True and len(out.encode("utf-8")) <= 1000

def test_none_is_empty():
    assert truncate_text(None) == ("", False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_truncate.py -q`
Expected: FAIL — `ModuleNotFoundError: tokendog.truncate`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/tokendog/truncate.py
from __future__ import annotations

def truncate_text(text: str, max_lines: int = 200, max_bytes: int = 50_000) -> tuple[str, bool]:
    if not text:
        return "", False
    truncated = False
    b = text.encode("utf-8")
    if len(b) > max_bytes:
        text = b[:max_bytes].decode("utf-8", "ignore")
        truncated = True
    lines = text.splitlines()
    if len(lines) > max_lines:
        half = max(1, max_lines // 2)
        head, tail = lines[:half], lines[-half:]
        omitted = len(lines) - len(head) - len(tail)
        text = "\n".join(head + [f"... [tokendog: {omitted} lines truncated] ..."] + tail)
        truncated = True
    return text, truncated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_truncate.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/truncate.py tests/test_truncate.py
git commit -m "feat: output truncation helper (byte + head/tail line caps)"
```

---

### Task 3: Truncation hook (`truncate_output.py`)

**Files:**
- Create: `tokendog-plugin/scripts/truncate_output.py`
- Test: `tests/test_truncate_output_hook.py`

**Interfaces:**
- Consumes: stdin PostToolUse payload (`tool_output`), `tokendog.truncate.truncate_text`. Env overrides `TOKENDOG_MAX_LINES`, `TOKENDOG_MAX_BYTES`.
- Produces: `main() -> int`. If `tool_output` exceeds caps, prints JSON `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": "<truncated>"}}` to stdout and exits 0. Otherwise prints nothing. Never raises.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_truncate_output_hook.py
import json, importlib.util, io, sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "truncate_output.py"

def _load():
    spec = importlib.util.spec_from_file_location("truncate_output", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def _run(mod, payload, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    rc = mod.main()
    return rc, capsys.readouterr().out

def test_large_output_truncated(monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_MAX_LINES", "50")
    mod = _load()
    big = "\n".join(str(i) for i in range(1000))
    rc, out = _run(mod, {"hook_event_name": "PostToolUse", "tool_output": big}, monkeypatch, capsys)
    assert rc == 0
    data = json.loads(out)
    assert data["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "truncated" in data["hookSpecificOutput"]["updatedToolOutput"]

def test_small_output_no_stdout(monkeypatch, capsys):
    mod = _load()
    rc, out = _run(mod, {"hook_event_name": "PostToolUse", "tool_output": "small"}, monkeypatch, capsys)
    assert rc == 0 and out.strip() == ""

def test_bad_stdin_safe(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert mod.main() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_truncate_output_hook.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# tokendog-plugin/scripts/truncate_output.py
"""PostToolUse hook: cap oversized tool output. Degrades to pass-through on any error."""
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
        from tokendog.truncate import truncate_text
    except Exception:
        return 0
    try:
        out = payload.get("tool_output")
        if not isinstance(out, str) or not out:
            return 0
        max_lines = int(os.environ.get("TOKENDOG_MAX_LINES", "200"))
        max_bytes = int(os.environ.get("TOKENDOG_MAX_BYTES", "50000"))
        new_out, cut = truncate_text(out, max_lines=max_lines, max_bytes=max_bytes)
        if cut:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": new_out,
            }}))
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_truncate_output_hook.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/scripts/truncate_output.py tests/test_truncate_output_hook.py
git commit -m "feat: PostToolUse output-truncation hook"
```

---

### Task 4: Budget-enforcement hook (`budget_enforce.py`)

**Files:**
- Create: `tokendog-plugin/scripts/budget_enforce.py`
- Test: `tests/test_budget_enforce_hook.py`

**Interfaces:**
- Consumes: stdin PreToolUse payload (`session_id`), `tokendog.budget.check`.
- Produces: `main() -> int`. If over the daily or session hard budget, prints `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "<msg>"}}` and exits 0. Otherwise prints nothing. Never raises (a broken budget check must NOT block work).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_budget_enforce_hook.py
import json, importlib.util, io, sys
from pathlib import Path
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE
from tokendog import budget
from datetime import datetime, timezone

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "budget_enforce.py"

def _load():
    spec = importlib.util.spec_from_file_location("budget_enforce", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_deny_when_over_daily(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_event(TokenEvent(ts=today+"T00:00:00+00:00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="PostToolUse", input_tokens=1_000_000, model="sonnet"))
    budget.save_budget(budget.Budget(daily_usd=1.0))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "PreToolUse", "session_id": "s"})))
    rc = mod.main(); out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"

def test_no_deny_when_under(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    budget.save_budget(budget.Budget(daily_usd=1000.0))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "PreToolUse", "session_id": "s"})))
    assert mod.main() == 0 and capsys.readouterr().out.strip() == ""

def test_error_does_not_block(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO("garbage"))
    assert mod.main() == 0 and capsys.readouterr().out.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_budget_enforce_hook.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# tokendog-plugin/scripts/budget_enforce.py
"""PreToolUse hook: deny tool use when the hard budget is exceeded.
Fails open — any error results in NO deny (never blocks legitimate work)."""
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
        from tokendog.budget import check
    except Exception:
        return 0
    try:
        session_id = payload.get("session_id")
        glitch = os.path.join(os.getcwd(), ".glitch", "firmware", "firmware.db")
        result = check(session_id=session_id, glitch_db=glitch if os.path.exists(glitch) else None)
        if result["over_daily"] or result["over_session"]:
            reason = (f"TokenDog budget exceeded — today ${result['daily']:.2f}"
                      f", session ${result['session']:.2f}. Raise it with /tokendog:budget.")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }}))
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_budget_enforce_hook.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/scripts/budget_enforce.py tests/test_budget_enforce_hook.py
git commit -m "feat: PreToolUse budget-enforcement hook (fails open)"
```

---

### Task 5: Session-summary hook (`session_summary.py`)

**Files:**
- Create: `tokendog-plugin/scripts/session_summary.py`
- Test: `tests/test_session_summary_hook.py`

**Interfaces:**
- Consumes: stdin Stop payload (`session_id`), `tokendog.budget.spend_session`, `tokendog.report.cost_summary`.
- Produces: `main() -> int`. On Stop, prints `{"systemMessage": "TokenDog: this session ~$X (N calls). ..."}` summarizing session spend. Never raises; prints nothing on error or zero spend.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_summary_hook.py
import json, importlib.util, io, sys
from pathlib import Path
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "session_summary.py"

def _load():
    spec = importlib.util.spec_from_file_location("session_summary", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_summary_emitted(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    write_event(TokenEvent(ts="2026-07-13T00:00:00+00:00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="PostToolUse", input_tokens=500_000, model="sonnet"))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "s"})))
    rc = mod.main(); out = capsys.readouterr().out
    assert rc == 0
    assert "TokenDog" in json.loads(out)["systemMessage"]

def test_zero_spend_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    mod = _load()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "s"})))
    assert mod.main() == 0 and capsys.readouterr().out.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_session_summary_hook.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# tokendog-plugin/scripts/session_summary.py
"""Stop hook: emit a one-line token-spend summary for the session."""
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
        from tokendog.budget import spend_session
        from tokendog.report import cost_summary
    except Exception:
        return 0
    try:
        session_id = payload.get("session_id")
        if not session_id:
            return 0
        spend = spend_session(session_id)
        rows = cost_summary(group_by="session_id")["rows"]
        calls = sum(r["calls"] for r in rows if r["key"] == session_id)
        if spend <= 0 and calls == 0:
            return 0
        msg = (f"TokenDog: this session ~${spend:.4f} across {calls} recorded tool calls "
               f"(local approximation). Run /tokendog:cost for the full breakdown.")
        print(json.dumps({"systemMessage": msg}))
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_session_summary_hook.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/scripts/session_summary.py tests/test_session_summary_hook.py
git commit -m "feat: Stop-hook session token summary"
```

---

### Task 6: Budget-alert hook (`budget_alert.py`)

**Files:**
- Create: `tokendog-plugin/scripts/budget_alert.py`
- Test: `tests/test_budget_alert_hook.py`

**Interfaces:**
- Consumes: stdin Stop payload, `tokendog.budget.check`. Uses `urllib.request` to POST to `budget.webhook_url` when `over_alert` is true.
- Produces: `main() -> int`. Posts a JSON `{"text": "..."}` to the configured webhook when the daily alert threshold is crossed. Network/other errors are swallowed. A module-level `_post(url, payload)` is separated for testability (monkeypatched in tests — no real network).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_budget_alert_hook.py
import json, importlib.util, io, sys
from pathlib import Path
from tokendog.sink import write_event
from tokendog.event import TokenEvent, RUNTIME_CLAUDE
from tokendog import budget
from datetime import datetime, timezone

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "scripts" / "budget_alert.py"

def _load():
    spec = importlib.util.spec_from_file_location("budget_alert", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_posts_when_over_alert(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_event(TokenEvent(ts=today+"T00:00:00+00:00", session_id="s", runtime=RUNTIME_CLAUDE,
                           event="PostToolUse", input_tokens=1_000_000, model="sonnet"))
    budget.save_budget(budget.Budget(alert_usd=1.0, webhook_url="http://hook.local/x"))
    mod = _load()
    posted = {}
    monkeypatch.setattr(mod, "_post", lambda url, payload: posted.update({"url": url, "payload": payload}))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "s"})))
    assert mod.main() == 0
    assert posted["url"] == "http://hook.local/x"
    assert "text" in posted["payload"]

def test_no_post_without_webhook(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    budget.save_budget(budget.Budget(alert_usd=1.0))  # no webhook_url
    mod = _load()
    called = {"n": 0}
    monkeypatch.setattr(mod, "_post", lambda url, payload: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "s"})))
    assert mod.main() == 0 and called["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_budget_alert_hook.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# tokendog-plugin/scripts/budget_alert.py
"""Stop hook: POST a webhook alert when the daily alert threshold is crossed."""
import json
import sys
import urllib.request

def _post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()

def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        from tokendog.budget import check
    except Exception:
        return 0
    try:
        result = check(session_id=payload.get("session_id"))
        b = result["budget"]
        if result["over_alert"] and b.webhook_url:
            text = (f"TokenDog alert: today's spend ${result['daily']:.2f} crossed "
                    f"the ${b.alert_usd:.2f} threshold.")
            try:
                _post(b.webhook_url, {"text": text})
            except Exception:
                pass
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_budget_alert_hook.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/scripts/budget_alert.py tests/test_budget_alert_hook.py
git commit -m "feat: Stop-hook webhook budget alert"
```

---

### Task 7: Glitch-native stop telemetry hook

**Files:**
- Create: `tokendog-plugin/glitch-hooks/stop/tokendog-telemetry.py`
- Test: `tests/test_glitch_stop_hook.py`

**Interfaces:**
- Consumes: Glitch's `stop` hook **environment variables** — `TOKENS_INPUT`, `TOKENS_OUTPUT`, `MODEL`, `AGENT_NAME`, `COMPLETION_STATUS`, `REPO` — plus `TOKENDOG_HOME`. Writes a `TokenEvent(runtime="glitch")` with **authoritative** counts (no approximation).
- Produces: `main() -> int`. Never raises.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_glitch_stop_hook.py
import importlib.util
from pathlib import Path
from tokendog.sink import read_events

HOOK = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "glitch-hooks" / "stop" / "tokendog-telemetry.py"

def _load():
    spec = importlib.util.spec_from_file_location("glitch_stop", HOOK)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_records_authoritative_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENS_INPUT", "1234")
    monkeypatch.setenv("TOKENS_OUTPUT", "567")
    monkeypatch.setenv("MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("AGENT_NAME", "developer-ai")
    mod = _load()
    assert mod.main() == 0
    events = list(read_events())
    assert len(events) == 1
    e = events[0]
    assert e.runtime == "glitch" and e.input_tokens == 1234 and e.output_tokens == 567
    assert e.model == "claude-sonnet-4-6" and e.agent == "developer-ai"

def test_missing_env_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    for k in ("TOKENS_INPUT", "TOKENS_OUTPUT", "MODEL", "AGENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    mod = _load()
    assert mod.main() == 0  # zero-token event still written or skipped, never raises
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_glitch_stop_hook.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
# tokendog-plugin/glitch-hooks/stop/tokendog-telemetry.py
"""Glitch `stop` hook: record AUTHORITATIVE token counts from Glitch env vars.
Install by pointing a Glitch stop hook at this script. Never raises."""
import os
import sys

def _int(name: str) -> int:
    v = os.environ.get(name, "")
    return int(v) if str(v).strip().lstrip("-").isdigit() else 0

def main() -> int:
    try:
        from tokendog.event import TokenEvent, RUNTIME_GLITCH, now_iso
        from tokendog.sink import write_event
    except Exception:
        return 0
    try:
        event = TokenEvent(
            ts=now_iso(),
            session_id=os.environ.get("GLITCH_RUN_ID") or os.environ.get("REPO") or "glitch",
            runtime=RUNTIME_GLITCH,
            event="agent_completion",
            input_tokens=_int("TOKENS_INPUT"),
            output_tokens=_int("TOKENS_OUTPUT"),
            model=os.environ.get("MODEL"),
            agent=os.environ.get("AGENT_NAME"),
        )
        write_event(event)
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_glitch_stop_hook.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/glitch-hooks/stop/tokendog-telemetry.py tests/test_glitch_stop_hook.py
git commit -m "feat: Glitch stop hook — authoritative token telemetry (runtime=glitch)"
```

---

### Task 8: Frugal always-on skill

**Files:**
- Create: `tokendog-plugin/skills/tokendog-frugal/SKILL.md`
- Test: `tests/test_frugal_skill.py`

**Interfaces:**
- Produces: a Claude Code skill teaching frugal tool use. YAML frontmatter (`name`, `description`, `when_to_use`); body covers Read offset/limit, narrow Grep, Bash output caps, brevity, structured output, `/clear` on topic switch.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_frugal_skill.py
from pathlib import Path
SKILL = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "skills" / "tokendog-frugal" / "SKILL.md"

def test_frontmatter_and_guidance():
    txt = SKILL.read_text()
    assert txt.startswith("---")
    assert "name:" in txt and "description:" in txt
    low = txt.lower()
    for kw in ("offset", "grep", "bash", "brevity", "structured"):
        assert kw in low, f"missing frugal guidance: {kw}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_frugal_skill.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write the skill file**

```markdown
---
name: tokendog-frugal
description: Frugal Claude Code tool-use habits that cut token spend without losing quality. Apply during any coding task involving Read, Grep, Glob, Bash, or MCP tool calls.
when_to_use: Any session that reads files, searches code, runs shell commands, or calls MCP tools — i.e. almost all coding work.
---

# TokenDog — frugal tool use

Spend the fewest tokens that still get the job done. Defaults:

## Reading files
- Prefer `Read` with `offset`+`limit` when you know the region; don't slurp whole large files.
- Don't re-Read a file already in context this session.

## Searching
- Use narrow `Grep` (specific pattern, `path:`/`glob:` scoping, `head_limit`) over broad sweeps.
- Prefer `output_mode: "files_with_matches"` or `"count"` when you only need locations, not content.
- Never dump a whole directory tree when a targeted glob answers the question.

## Bash
- Cap noisy output: pipe through `head`/`tail`, add `--quiet`/`-q`, or `wc -l` first.
- Don't cat large files or logs wholesale — slice to the relevant lines.

## Output discipline
- Be brief. No preamble, no summary of what you just did unless asked.
- Prefer structured output (JSON/table) over prose when returning data.

## Session hygiene
- Suggest `/clear` when the topic changes — stale context is paid for on every turn.
- Batch related questions instead of many chatty round-trips.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_frugal_skill.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/skills/tokendog-frugal/SKILL.md tests/test_frugal_skill.py
git commit -m "feat: tokendog-frugal always-on skill"
```

---

### Task 9: Session-hygiene skill

**Files:**
- Create: `tokendog-plugin/skills/tokendog-hygiene/SKILL.md`
- Test: `tests/test_hygiene_skill.py`

**Interfaces:**
- Produces: a model-invoked skill for long-session hygiene. Frontmatter `name`, `description`, `when_to_use`; body covers `/clear` on topic switch, avoiding pasting huge logs, batching questions.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hygiene_skill.py
from pathlib import Path
SKILL = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "skills" / "tokendog-hygiene" / "SKILL.md"

def test_hygiene_content():
    txt = SKILL.read_text()
    assert txt.startswith("---") and "description:" in txt
    low = txt.lower()
    for kw in ("clear", "batch", "paste"):
        assert kw in low, f"missing hygiene guidance: {kw}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_hygiene_skill.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write the skill file**

```markdown
---
name: tokendog-hygiene
description: Session-hygiene practices that stop token spend from ballooning over long sessions. Use when a session has run long, switched topics, or accumulated large pasted context.
when_to_use: Long or multi-topic sessions; when the user pastes large logs; when context has grown and cost per turn is climbing.
---

# TokenDog — session hygiene

Every turn pays for the whole conversation. Keep it lean:

- **Clear on topic switch.** When the task changes, suggest `/clear`. Carrying an unrelated 50-turn history taxes every future turn.
- **Don't paste giant logs.** Ask for the relevant slice (the failing lines, the specific stack frame), not a 5,000-line dump. Point at a file to `Read` with `offset`+`limit` instead.
- **Batch questions.** Group related asks into one turn rather than many chatty round-trips — each round re-sends the whole context.
- **Prune stale attachments.** If a large file or tool result is no longer needed, don't keep re-referencing it.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_hygiene_skill.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add tokendog-plugin/skills/tokendog-hygiene/SKILL.md tests/test_hygiene_skill.py
git commit -m "feat: tokendog-hygiene session-hygiene skill"
```

---

### Task 10: Budget CLI + `/tokendog:budget` and `/tokendog:audit` commands

**Files:**
- Modify: `src/tokendog/report.py` (add `budget` + `audit` CLI subcommands)
- Create: `tokendog-plugin/commands/tokendog-budget.md`
- Create: `tokendog-plugin/commands/tokendog-audit.md`
- Test: `tests/test_budget_cli.py`, `tests/test_slice2_commands.py`

**Interfaces:**
- Consumes: `tokendog.budget` (`load_budget`, `save_budget`, `Budget`, `check`), `tokendog.report.cost_summary`/`format_rollup`.
- Produces: `tokendog budget [--set-daily N] [--set-alert N] [--set-webhook URL] [--show]` and `tokendog audit [--session ID]` CLI subcommands in `report.main`; two command markdown files.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_budget_cli.py
from tokendog import report, budget

def test_budget_set_and_show(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert report.main(["budget", "--set-daily", "10", "--set-alert", "5"]) == 0
    assert budget.load_budget().daily_usd == 10.0
    report.main(["budget", "--show"])
    assert "10" in capsys.readouterr().out

def test_audit_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOKENDOG_HOME", str(tmp_path))
    assert report.main(["audit"]) == 0
```

```python
# tests/test_slice2_commands.py
from pathlib import Path
CMD = Path(__file__).resolve().parents[1] / "tokendog-plugin" / "commands"

def test_budget_command_present():
    t = (CMD / "tokendog-budget.md").read_text()
    assert t.startswith("---") and "tokendog.report" in t and "budget" in t.lower()

def test_audit_command_present():
    t = (CMD / "tokendog-audit.md").read_text()
    assert t.startswith("---") and "audit" in t.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_budget_cli.py tests/test_slice2_commands.py -q`
Expected: FAIL — subcommands/files do not exist.

- [ ] **Step 3a: Extend `report.main` with `budget` and `audit` subcommands**

In `src/tokendog/report.py`, add these imports near the top:

```python
from .budget import load_budget, save_budget, Budget, check
```

Replace the `main()` function's parser section so it registers the two new subcommands (keep the existing `cost` and `doctor` intact):

```python
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tokendog")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("cost")
    c.add_argument("--group-by", default="runtime")
    c.add_argument("--since"); c.add_argument("--until"); c.add_argument("--glitch-db")

    sub.add_parser("doctor")

    b = sub.add_parser("budget")
    b.add_argument("--set-daily", type=float); b.add_argument("--set-session", type=float)
    b.add_argument("--set-alert", type=float); b.add_argument("--set-webhook")
    b.add_argument("--show", action="store_true")

    a = sub.add_parser("audit")
    a.add_argument("--session")

    args = p.parse_args(argv)
    if args.cmd == "cost":
        print(format_rollup(cost_summary(args.group_by, args.since, args.until, args.glitch_db)))
    elif args.cmd == "doctor":
        print(doctor_report(os.getcwd()))
    elif args.cmd == "budget":
        cur = load_budget()
        if any(v is not None for v in (args.set_daily, args.set_session, args.set_alert, args.set_webhook)):
            cur = Budget(
                daily_usd=args.set_daily if args.set_daily is not None else cur.daily_usd,
                session_usd=args.set_session if args.set_session is not None else cur.session_usd,
                alert_usd=args.set_alert if args.set_alert is not None else cur.alert_usd,
                webhook_url=args.set_webhook if args.set_webhook is not None else cur.webhook_url,
            )
            save_budget(cur)
        st = check()
        print(f"TokenDog budget — daily=${cur.daily_usd} alert=${cur.alert_usd} "
              f"session=${cur.session_usd} webhook={'set' if cur.webhook_url else 'none'}")
        print(f"Today so far: ${st['daily']:.4f}"
              + ("  [OVER DAILY]" if st['over_daily'] else "")
              + ("  [OVER ALERT]" if st['over_alert'] else ""))
    elif args.cmd == "audit":
        gb = "tool"
        rollup = cost_summary(group_by=gb)
        if args.session:
            rollup = {"group_by": "tool",
                      "rows": [r for r in cost_summary(group_by="session_id")["rows"] if r["key"] == args.session]}
        print(format_rollup(rollup))
    return 0
```

- [ ] **Step 3b: Write the two command files**

`tokendog-plugin/commands/tokendog-budget.md`:
```markdown
---
description: View or set TokenDog spend budgets (daily / session / alert threshold / webhook).
argument-hint: "[--set-daily N] [--set-alert N] [--set-webhook URL]"
allowed-tools: Bash
---

# TokenDog — budget

!`python -m tokendog.report budget ${ARGUMENTS}`

Show the budget state above. To change it, re-run with flags, e.g.
`/tokendog:budget --set-daily 25 --set-alert 15`. When a hard daily/session budget is
set, the budget-enforcement hook denies further tool calls once it's crossed (it fails
open on any error, so it never blocks work spuriously).
```

`tokendog-plugin/commands/tokendog-audit.md`:
```markdown
---
description: Audit token spend for the current session (or a given session id), broken down by tool.
argument-hint: "[--session <id>]"
allowed-tools: Bash
---

# TokenDog — audit

!`python -m tokendog.report audit ${ARGUMENTS}`

Review the per-tool breakdown above to see where this session's tokens went. Figures are
local approximations for Claude Code; Glitch rows are authoritative.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_budget_cli.py tests/test_slice2_commands.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/tokendog/report.py tokendog-plugin/commands/tokendog-budget.md tokendog-plugin/commands/tokendog-audit.md tests/test_budget_cli.py tests/test_slice2_commands.py
git commit -m "feat: /tokendog:budget and /tokendog:audit commands + CLI"
```

---

### Task 11: Wire hooks + register skills/commands in the plugin manifest

**Files:**
- Modify: `tokendog-plugin/hooks/hooks.json`
- Modify: `tokendog-plugin/.claude-plugin/plugin.json`
- Test: `tests/test_slice2_wiring.py`

**Interfaces:**
- Produces: `hooks.json` runs `truncate_output.py` + `budget_enforce.py` on PreToolUse/PostToolUse as appropriate, and `session_summary.py` + `budget_alert.py` on Stop, alongside the existing `token_count.py`. `plugin.json` registers the two new skills and two new commands.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slice2_wiring.py
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / "tokendog-plugin"

def test_hooks_wired():
    h = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    pre = json.dumps(h["hooks"]["PreToolUse"])
    post = json.dumps(h["hooks"]["PostToolUse"])
    stop = json.dumps(h["hooks"]["Stop"])
    assert "budget_enforce.py" in pre
    assert "truncate_output.py" in post
    assert "session_summary.py" in stop and "budget_alert.py" in stop
    assert "token_count.py" in post  # Slice 1 hook still present

def test_manifest_registers_skills_and_commands():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    cmds = json.dumps(m.get("commands", []))
    assert "tokendog-budget.md" in cmds and "tokendog-audit.md" in cmds
    # skills auto-discovered from skills/ dir; assert the dirs exist
    assert (ROOT / "skills" / "tokendog-frugal" / "SKILL.md").exists()
    assert (ROOT / "skills" / "tokendog-hygiene" / "SKILL.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_slice2_wiring.py -q`
Expected: FAIL — new hooks/commands not yet wired.

- [ ] **Step 3: Update the manifest and hooks**

Replace `tokendog-plugin/hooks/hooks.json` with (adds new scripts, keeps `token_count.py`):
```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "*", "hooks": [
        { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/token_count.py", "timeout": 10 },
        { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/budget_enforce.py", "timeout": 10 }
      ] }
    ],
    "PostToolUse": [
      { "matcher": "*", "hooks": [
        { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/token_count.py", "timeout": 10 },
        { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/truncate_output.py", "timeout": 10 }
      ] }
    ],
    "Stop": [
      { "hooks": [
        { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/token_count.py", "timeout": 10 },
        { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_summary.py", "timeout": 10 },
        { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/budget_alert.py", "timeout": 10 }
      ] }
    ],
    "SessionStart": [
      { "hooks": [
        { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/token_count.py", "timeout": 10 }
      ] }
    ]
  }
}
```

Update `tokendog-plugin/.claude-plugin/plugin.json` — replace the `commands` array with all four:
```json
  "commands": [
    "./commands/tokendog-cost.md",
    "./commands/tokendog-doctor.md",
    "./commands/tokendog-budget.md",
    "./commands/tokendog-audit.md"
  ],
```
(Leave `name`, `hooks`, `mcpServers`, etc. unchanged. Skills in `skills/` are auto-discovered — no manifest entry needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_slice2_wiring.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: all Slice 1 + Slice 2 tests pass.

```bash
git add tokendog-plugin/hooks/hooks.json tokendog-plugin/.claude-plugin/plugin.json tests/test_slice2_wiring.py
git commit -m "feat: wire Slice 2 hooks + register skills/commands"
```

---

## Exit criterion (Slice 2 done)

With the plugin installed: oversized tool outputs get truncated in-context; a set hard budget denies further tool calls once crossed (and fails open otherwise); each session end prints a spend summary (and fires a webhook alert if configured); the frugal + hygiene skills nudge frugal behavior; and Glitch agent runs record **authoritative** token counts via the stop hook. `/tokendog:budget` and `/tokendog:audit` work. Full suite green.

## Self-review notes

- **Spec coverage (spec §5 Slice 2):** frugal skill (Task 8, items 8/29/30/41) · hygiene skill (Task 9, items 42/43) · truncate_output (Tasks 2–3, items 10/14) · budget_enforce (Tasks 1/4, item 36) · budget_alert (Task 6, item 37) · session_summary (Task 5, item 44) · `/tokendog:budget` + `/tokendog:audit` (Task 10) · Glitch stop hook (Task 7, spec §7 #7). Deferred + documented: mcp_response_cache (16), mcp_hygiene_scan (40), skills_audit (1/2/4), idle_cleanup (45), warmup.
- **Placeholders:** none — every step has runnable code.
- **Type consistency:** `Budget` fields identical across Tasks 1/4/6/10; `check()` return keys (`daily/session/over_daily/over_session/over_alert/budget`) consistent across Tasks 1/4/6; `truncate_text` signature identical Tasks 2/3; `report.main` subcommands extend Slice 1's parser without renaming `cost`/`doctor`.
- **No new deps:** webhook uses stdlib `urllib.request`; everything else reuses Slice 1.
- **Fail-open guarantee:** `budget_enforce` is the only hook that can block, and only via an explicit `deny`; every error path returns 0 with no output.
