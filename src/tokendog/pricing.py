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
