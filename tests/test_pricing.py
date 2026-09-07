from tokendog.pricing import (
    estimate_cost,
    PRICES,
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_5M_MULTIPLIER,
    CACHE_WRITE_1H_MULTIPLIER,
)


def test_uses_default_when_model_unknown():
    pin, pout = PRICES["default"]
    got = estimate_cost(1_000_000, 0, model=None)
    assert abs(got - pin) < 1e-9


def test_output_priced():
    pin, pout = PRICES["default"]
    assert abs(estimate_cost(0, 1_000_000, None) - pout) < 1e-9


def test_zero_is_zero():
    assert estimate_cost(0, 0, "anything") == 0.0


# --- cache buckets ------------------------------------------------------
# Cache is where the money is: on a real agent workload ~85% of cost is
# cache write + cache read. Pricing them at zero (the old behaviour) makes
# every cost figure wrong by a large factor.


def test_cache_read_priced_at_one_tenth_of_input():
    pin, _ = PRICES["default"]
    got = estimate_cost(0, 0, None, cache_read_tokens=1_000_000)
    assert abs(got - pin * CACHE_READ_MULTIPLIER) < 1e-9


def test_cache_write_5m_priced_at_1_25x_input():
    pin, _ = PRICES["default"]
    got = estimate_cost(0, 0, None, cache_creation_5m_tokens=1_000_000)
    assert abs(got - pin * CACHE_WRITE_5M_MULTIPLIER) < 1e-9


def test_cache_write_1h_priced_at_2x_input():
    pin, _ = PRICES["default"]
    got = estimate_cost(0, 0, None, cache_creation_1h_tokens=1_000_000)
    assert abs(got - pin * CACHE_WRITE_1H_MULTIPLIER) < 1e-9


def test_1h_cache_write_costs_more_than_5m():
    """The whole reason the ephemeral split must survive ingestion: the two
    TTLs bill at different multipliers, so a single cache_creation scalar
    cannot be priced exactly."""
    five_m = estimate_cost(0, 0, None, cache_creation_5m_tokens=1_000_000)
    one_h = estimate_cost(0, 0, None, cache_creation_1h_tokens=1_000_000)
    assert one_h > five_m
    assert abs(one_h / five_m - (CACHE_WRITE_1H_MULTIPLIER / CACHE_WRITE_5M_MULTIPLIER)) < 1e-9


def test_all_four_buckets_sum():
    pin, pout = PRICES["default"]
    got = estimate_cost(
        1_000_000, 1_000_000, None,
        cache_read_tokens=1_000_000,
        cache_creation_5m_tokens=1_000_000,
        cache_creation_1h_tokens=1_000_000,
    )
    expected = (pin + pout + pin * CACHE_READ_MULTIPLIER
                + pin * CACHE_WRITE_5M_MULTIPLIER + pin * CACHE_WRITE_1H_MULTIPLIER)
    assert abs(got - expected) < 1e-9


# --- per-model rates ----------------------------------------------------


def test_cache_multipliers_scale_with_the_model_input_rate():
    opus_in, _ = PRICES["opus"]
    got = estimate_cost(0, 0, "claude-opus-5", cache_read_tokens=1_000_000)
    assert abs(got - opus_in * CACHE_READ_MULTIPLIER) < 1e-9


def test_matches_versioned_and_suffixed_model_ids():
    for model in ("claude-opus-5", "claude-opus-4-8", "claude-opus-5[1m]"):
        assert estimate_cost(1_000_000, 0, model) == PRICES["opus"][0]
    assert estimate_cost(1_000_000, 0, "claude-sonnet-5") == PRICES["sonnet"][0]
    assert estimate_cost(1_000_000, 0, "claude-haiku-4-5") == PRICES["haiku"][0]
    assert estimate_cost(1_000_000, 0, "claude-fable-5") == PRICES["fable"][0]


# --- claims the docs make -----------------------------------------------


def test_carrying_beats_generating_after_fifty_turns_on_opus():
    """README and docs/FEATURES.md both assert this; pin it to the rates.

    "A token you carry for 50 turns costs more than a token you generate."
    It is the sentence the three-lever ordering rests on, and it is a
    consequence of the rate table, not an independent fact — if Opus pricing or
    the cache-read multiplier moves, the docs become wrong silently.
    """
    carry_per_turn = estimate_cost(model="opus", cache_read_tokens=1_000_000)
    generate_once = estimate_cost(output_tokens=1_000_000, model="opus")
    break_even = generate_once / carry_per_turn
    assert break_even == 50, (
        f"docs say 50 turns; the rate table now says {break_even:g}. "
        "Update README.md and docs/FEATURES.md together with this test.")
