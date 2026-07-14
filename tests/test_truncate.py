from tokendog.truncate import truncate_text


def test_short_text_untouched():
    out, cut = truncate_text("a\nb\nc", max_lines=200, max_bytes=1000)
    assert out == "a\nb\nc" and cut is False


def test_line_cap_keeps_head_and_tail():
    text = "\n".join(str(i) for i in range(1000))
    out, cut = truncate_text(text, max_lines=100, max_bytes=10_000_000)
    assert cut is True
    assert "0" in out.splitlines()[0]
    assert "999" in out.splitlines()[-1]
    assert "truncated" in out


def test_byte_cap():
    out, cut = truncate_text("x" * 100_000, max_lines=100000, max_bytes=1000)
    assert cut is True and len(out.encode("utf-8")) <= 1000


def test_none_is_empty():
    assert truncate_text(None) == ("", False)
