from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
import sqlite3
from .event import TokenEvent
from .pricing import estimate_cost
from .config import db_path

_ALLOWED_GROUP = {"runtime", "user", "tool", "model", "pipeline",
                  "day", "session_id", "agent", "cluster", "source",
                  "service_tier", "inference_geo", "band", "transcript_id"}

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
    # The two buckets that dominate a real agent bill. Reported alongside
    # input/output so a consumer can see WHICH knob to move, rather than
    # being handed one scalar total that hides it.
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read_tokens + self.cache_creation_tokens)

@dataclass
class Rollup:
    group_by: str
    rows: list[RollupRow]

class CostBackend(Protocol):
    def ingest(self, event: TokenEvent) -> None: ...
    def query(self, filters: QueryFilter) -> Rollup: ...

_COLUMNS = ("ts", "session_id", "runtime", "event", "input_tokens", "output_tokens",
            "cache_read_tokens", "cache_creation_tokens", "cache_creation_5m_tokens",
            "cache_creation_1h_tokens", "tool_payload_tokens", "source",
            "service_tier", "inference_geo", "context_tokens", "band",
            "transcript_id", "tool", "model", "user",
            "pipeline", "run_id", "agent", "cluster", "file")

def _col_def(c: str) -> str:
    if c.endswith("_tokens"):
        return f"{c} INTEGER"
    if c == "est_cost_usd":
        return f"{c} REAL"
    return f"{c} TEXT"

_DB_COLUMNS = _COLUMNS + ("est_cost_usd",)


def event_cost(event: TokenEvent) -> float:
    """Price one event across all four billing buckets.

    Only authoritative events are priced. A hook event carries a tiktoken
    approximation of a tool payload whose bytes the runtime already bills on
    the following turn — pricing it here would double-count it, and pricing a
    tool result at the OUTPUT rate (as this used to) overstates it further.
    """
    if not event.is_authoritative:
        return 0.0
    return estimate_cost(
        event.input_tokens,
        event.output_tokens,
        event.model,
        cache_read_tokens=event.cache_read_tokens,
        cache_creation_5m_tokens=event.cache_creation_5m_tokens,
        cache_creation_1h_tokens=event.cache_creation_1h_tokens,
    )

class LocalSQLiteBackend:
    def __init__(self, path=None):
        self._conn = sqlite3.connect(str(path) if path is not None else str(db_path()))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events (%s)" %
            ", ".join(_col_def(c) for c in _DB_COLUMNS)
        )
        self._conn.commit()

    def ingest(self, event: TokenEvent) -> None:
        cost = round(event_cost(event), 6)
        vals = tuple(getattr(event, c) for c in _COLUMNS) + (cost,)
        self._conn.execute(
            "INSERT INTO events (%s) VALUES (%s)" % (", ".join(_DB_COLUMNS), ", ".join("?" * len(_DB_COLUMNS))),
            vals,
        )
        self._conn.commit()

    def query(self, filters: QueryFilter) -> Rollup:
        gb = filters.group_by
        if gb not in _ALLOWED_GROUP:
            raise ValueError(f"invalid group_by: {gb!r}")
        col = "substr(ts,1,10)" if gb == "day" else gb
        sql = (f"SELECT COALESCE({col},'(none)') AS k, COUNT(*), "
               f"COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
               f"COALESCE(SUM(cache_read_tokens),0), COALESCE(SUM(cache_creation_tokens),0), "
               f"COALESCE(SUM(est_cost_usd),0) FROM events")
        clauses, params = [], []
        if filters.since:
            clauses.append("ts >= ?"); params.append(filters.since)
        if filters.until:
            clauses.append("ts <= ?"); params.append(filters.until)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += (" GROUP BY k ORDER BY (COALESCE(SUM(input_tokens),0)"
                "+COALESCE(SUM(output_tokens),0)"
                "+COALESCE(SUM(cache_read_tokens),0)"
                "+COALESCE(SUM(cache_creation_tokens),0)) DESC")
        rows = []
        for k, calls, itok, otok, crtok, cctok, cost in self._conn.execute(sql, params):
            rows.append(RollupRow(key=str(k), calls=calls, input_tokens=itok,
                                  output_tokens=otok, est_cost_usd=round(cost, 6),
                                  cache_read_tokens=crtok, cache_creation_tokens=cctok))
        return Rollup(group_by=gb, rows=rows)
