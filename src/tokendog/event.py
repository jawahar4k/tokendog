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
