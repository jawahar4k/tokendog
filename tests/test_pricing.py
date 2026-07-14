from tokendog.pricing import estimate_cost, PRICES


def test_uses_default_when_model_unknown():
    pin, pout = PRICES["default"]
    got = estimate_cost(1_000_000, 0, model=None)
    assert abs(got - pin) < 1e-9


def test_output_priced():
    pin, pout = PRICES["default"]
    assert abs(estimate_cost(0, 1_000_000, None) - pout) < 1e-9


def test_zero_is_zero():
    assert estimate_cost(0, 0, "anything") == 0.0
