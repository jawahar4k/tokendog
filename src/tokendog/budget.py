from __future__ import annotations
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
import json
from pathlib import Path
from .config import tokendog_home
from .report import cost_summary

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
            return Budget(**{f.name: data.get(f.name) for f in fields(Budget)})
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
