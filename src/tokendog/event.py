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
    # Derived, for band roll-ups. Context is what the model had to be given
    # for this turn (cache_read + cache_creation + fresh input); output is
    # excluded because it came back rather than being carried. Set only for
    # metered turns — a hook event measures a tool payload, and a Glitch
    # context-load is not a turn, so both leave `band` as None and are
    # skipped by band reporting rather than inventing turns.
    context_tokens: int = 0
    band: str | None = None
    # One transcript file == one context that grew on its own.
    # NOT the same as session_id: a resumed session and its `agent-*` subagent
    # transcripts all carry the PARENT's sessionId, so grouping context peaks
    # by session_id merges separate contexts into one. Keep both — session_id
    # for spend attribution, transcript_id for "how big did a context get".
    transcript_id: str | None = None
    tool: str | None = None
    model: str | None = None
    user: str | None = None
    pipeline: str | None = None
    run_id: str | None = None
    agent: str | None = None
    cluster: str | None = None
    file: str | None = None
    # Basename of the transcript's `cwd`, e.g. "contextflow". Basename only:
    # a full path leaks the home directory, and often a client name with it.
    project: str | None = None
    # Tool names invoked by THIS response, in the order they appear. A response
    # can call several tools, and its blocks are spread across several records,
    # so the reader unions them while it holds the response. Empty for a
    # response that called nothing. This is the only place a transcript says
    # WHICH tool ran — `tool` below is set by hooks, which cover only sessions
    # started after the plugin was installed.
    tools: list[str] | None = None
    # How the session was started: "cli" for an interactive one, an sdk value
    # for a non-interactive/print-mode run. Carried because a headless run and
    # an interactive session need OPPOSITE advice — an exited run holds no
    # window, so telling someone to reset it is noise — and nothing else in the
    # event distinguishes them.
    entrypoint: str | None = None

    def __post_init__(self) -> None:
        if self.source == SOURCE_TRANSCRIPT:
            from .bands import band_for, context_tokens as _ctx
            self.context_tokens = _ctx(self.input_tokens, self.cache_read_tokens,
                                       self.cache_creation_tokens)
            self.band = band_for(self.context_tokens)

    @property
    def is_authoritative(self) -> bool:
        return self.source in AUTHORITATIVE_SOURCES

    @property
    def is_turn(self) -> bool:
        """A metered turn with a context window of its own."""
        return self.band is not None

    @property
    def is_headless(self) -> bool:
        """True for a non-interactive run (print mode / the SDK).

        Unknown entrypoints read as interactive: the conservative direction,
        since the advice for an interactive session (reset it) is harmless
        against a run that has already exited, while the reverse is not.
        """
        return bool(self.entrypoint) and self.entrypoint != "cli"

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @staticmethod
    def from_json(line: str) -> "TokenEvent":
        return TokenEvent(**json.loads(line))
