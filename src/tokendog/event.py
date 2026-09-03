from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json

RUNTIME_CLAUDE = "claude-code"
RUNTIME_GLITCH = "glitch"

# Where the numbers came from. This decides whether an event is BILLABLE.
#   transcript / glitch -> authoritative usage straight from the runtime; priced.
#   hook                -> a local tiktoken approximation of a tool payload.
#                          NOT priced: the same bytes are already billed by the
#                          transcript on the next turn, so pricing them here
#                          would double-count. Kept for attribution only
#                          ("which tool produced how much text").
SOURCE_HOOK = "hook"
SOURCE_TRANSCRIPT = "transcript"
SOURCE_GLITCH = "glitch"
AUTHORITATIVE_SOURCES = (SOURCE_TRANSCRIPT, SOURCE_GLITCH)


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
    # Total cache creation, and the ephemeral TTL split that composes it.
    # The split is kept because the two TTLs bill at different multipliers
    # (1.25x input for 5m, 2x for 1h) — the total alone cannot be priced.
    cache_creation_tokens: int = 0
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    # Approximate size of a tool payload observed by a hook. Never billed.
    tool_payload_tokens: int = 0
    source: str = SOURCE_HOOK
    # Both affect price and neither is derivable downstream, so they are
    # carried verbatim from the source rather than assumed.
    service_tier: str | None = None
    inference_geo: str | None = None
    tool: str | None = None
    model: str | None = None
    user: str | None = None
    pipeline: str | None = None
    run_id: str | None = None
    agent: str | None = None
    cluster: str | None = None
    file: str | None = None

    @property
    def is_authoritative(self) -> bool:
        return self.source in AUTHORITATIVE_SOURCES

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @staticmethod
    def from_json(line: str) -> "TokenEvent":
        return TokenEvent(**json.loads(line))
