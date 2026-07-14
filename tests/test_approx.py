from tokendog.approx import approx_tokens


def test_empty_is_zero():
    assert approx_tokens("") == 0


def test_nonempty_positive():
    assert approx_tokens("hello world, this is a test") > 0


def test_longer_text_more_tokens():
    assert approx_tokens("word " * 100) > approx_tokens("word " * 5)
