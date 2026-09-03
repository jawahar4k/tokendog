from __future__ import annotations
from typing import Iterable

# Context-size bands.
#
# The roll-up dimensions TokenDog had (runtime / tool / session / model) all
# answer "who spent it". None of them answer "how big was the context when it
# was spent" — and context size is the strongest predictor of consumption in an
# agent loop, because every token that enters a context is re-read on every
# later turn of that session.
#
# `context` for a turn is what the model had to be given to produce it:
#     cache_read + cache_creation + fresh input
# Output is excluded — it is what came back, not what was carried.

BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("<50k", 0, 50_000),
    ("50-100k", 50_000, 100_000),
    ("100-150k", 100_000, 150_000),
    ("150-200k", 150_000, 200_000),
    ("200-400k", 200_000, 400_000),
    ("400k+", 400_000, None),
)

BAND_LABELS = tuple(label for label, _, _ in BANDS)
BAND_ORDER = {label: i for i, label in enumerate(BAND_LABELS)}

# Turns at or above this context size are the "large" tail the headline calls
# out. It is a fixed, stated cut so the number is reproducible.
LARGE_CONTEXT_THRESHOLD = 200_000


def context_tokens(input_tokens: int = 0, cache_read_tokens: int = 0,
                   cache_creation_tokens: int = 0) -> int:
    """Tokens the model had to be given for this turn."""
    return int(input_tokens or 0) + int(cache_read_tokens or 0) + int(cache_creation_tokens or 0)


def band_for(context: int) -> str:
    """Label the band a context size falls in. Lower bound inclusive."""
    n = int(context or 0)
    if n < 0:
        n = 0
    for label, lower, upper in BANDS:
        if n >= lower and (upper is None or n < upper):
            return label
    return BAND_LABELS[-1]


def percentile(values: list[int], p: float) -> int:
    """Nearest-rank percentile. Returns 0 for an empty input."""
    if not values:
        return 0
    ordered = sorted(values)
    if p <= 0:
        return ordered[0]
    if p >= 100:
        return ordered[-1]
    # nearest-rank: smallest value at or above p% of the way through
    k = max(1, -(-int(len(ordered) * p) // 100))
    return ordered[min(k, len(ordered)) - 1]


def band_summary(events: Iterable) -> dict:
    """Histogram turns and context tokens by band, plus session-peak context.

    Only metered turns are counted — a hook event measures a tool payload and
    a Glitch context-load is not a turn, so including either would invent
    turns that never happened and skew every percentage.
    """
    per_band_turns: dict[str, int] = {label: 0 for label in BAND_LABELS}
    per_band_tokens: dict[str, int] = {label: 0 for label in BAND_LABELS}
    # Peaks are keyed on transcript, not session: a resumed session and its
    # subagent transcripts share one sessionId, so keying on session_id would
    # merge separate contexts and report a single inflated peak for all of them.
    peak: dict[str, int] = {}
    turns = 0
    total_context = 0
    large_turns = 0
    large_context = 0

    for e in events:
        if not getattr(e, "is_turn", False):
            continue
        ctx = context_tokens(e.input_tokens, e.cache_read_tokens, e.cache_creation_tokens)
        label = band_for(ctx)
        turns += 1
        total_context += ctx
        per_band_turns[label] += 1
        per_band_tokens[label] += ctx
        if ctx >= LARGE_CONTEXT_THRESHOLD:
            large_turns += 1
            large_context += ctx
        key = getattr(e, "transcript_id", None) or e.session_id or "unknown"
        if ctx > peak.get(key, 0):
            peak[key] = ctx

    def pct(part: int, whole: int) -> float:
        return (part / whole * 100.0) if whole else 0.0

    rows = [{
        "band": label,
        "turns": per_band_turns[label],
        "pct_turns": pct(per_band_turns[label], turns),
        "context_tokens": per_band_tokens[label],
        "pct_tokens": pct(per_band_tokens[label], total_context),
    } for label in BAND_LABELS]

    peaks = list(peak.values())
    return {
        "turns": turns,
        "total_context_tokens": total_context,
        "rows": rows,
        "concentration": {
            "threshold": LARGE_CONTEXT_THRESHOLD,
            "turns": large_turns,
            "pct_turns": pct(large_turns, turns),
            "context_tokens": large_context,
            "pct_tokens": pct(large_context, total_context),
        },
        "peak_context": {
            "unit": "transcript",
            "count": len(peaks),
            "p50": percentile(peaks, 50),
            "p90": percentile(peaks, 90),
            "max": max(peaks) if peaks else 0,
        },
    }
