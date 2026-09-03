from __future__ import annotations

# USD per 1,000,000 tokens as (input, output), Anthropic list rates.
# CONFIGURABLE DEFAULTS — verify/override against current Anthropic pricing.
# Matching is substring-based on the model id, so "claude-opus-5",
# "claude-opus-4-8" and "claude-opus-5[1m]" all resolve to the opus row.
PRICES: dict[str, tuple[float, float]] = {
    "fable": (10.0, 50.0),
    "mythos": (10.0, 50.0),
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
    "default": (3.0, 15.0),
}

# Cache is billed as a multiple of the model's INPUT rate:
#   - a cache read is ~0.1x input
#   - a cache write is 1.25x input for the 5-minute TTL, 2x for the 1-hour TTL
# The two write multipliers are why the ephemeral 5m/1h split has to survive
# ingestion: a single `cache_creation_input_tokens` scalar cannot be priced
# exactly, because it conflates two different rates.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.0


def _match(model: str | None) -> tuple[float, float]:
    if model:
        m = model.lower()
        for key, price in PRICES.items():
            if key != "default" and key in m:
                return price
    return PRICES["default"]


def estimate_cost(
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: str | None = None,
    *,
    cache_read_tokens: int = 0,
    cache_creation_5m_tokens: int = 0,
    cache_creation_1h_tokens: int = 0,
) -> float:
    """Cost in USD for one metered turn, across all four billing buckets.

    Fresh input and output are billed at the model's own rates; cache reads and
    cache writes are billed as multiples of the input rate (see the constants
    above). Omitting the cache buckets prices them at zero — which is only
    correct for a turn that genuinely had no cache activity.
    """
    pin, pout = _match(model)
    per_m = 1_000_000
    return (
        (input_tokens / per_m) * pin
        + (output_tokens / per_m) * pout
        + (cache_read_tokens / per_m) * pin * CACHE_READ_MULTIPLIER
        + (cache_creation_5m_tokens / per_m) * pin * CACHE_WRITE_5M_MULTIPLIER
        + (cache_creation_1h_tokens / per_m) * pin * CACHE_WRITE_1H_MULTIPLIER
    )
