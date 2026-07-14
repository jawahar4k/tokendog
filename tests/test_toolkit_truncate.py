from tokendog_mcp.truncate import cap_items, cap_text

def test_cap_items():
    got, cut = cap_items(list(range(500)), max_items=100)
    assert len(got) == 100 and cut is True
    got2, cut2 = cap_items([1, 2], max_items=100)
    assert got2 == [1, 2] and cut2 is False

def test_cap_text():
    out, cut = cap_text("x" * 1000, max_bytes=100)
    assert len(out.encode("utf-8")) <= 100 and cut is True
    out2, cut2 = cap_text("short", max_bytes=100)
    assert out2 == "short" and cut2 is False

def test_cap_text_multibyte_boundary():
    # "é" is 2 bytes (0xC3 0xA9); cut mid-character must not drop valid preceding chars
    text = "aé"  # 3 bytes: 'a'(1) + 'é'(2)
    out, cut = cap_text(text, max_bytes=2)  # fits 'a' + first byte of 'é'
    assert out == "a", f"Expected 'a', got {out!r}"
    assert cut is True
    assert out.encode("utf-8") == b"a"

def test_cap_text_multibyte_fits_exactly():
    text = "aé"  # 3 bytes
    out, cut = cap_text(text, max_bytes=3)
    assert out == "aé" and cut is False

def test_cap_text_emoji_mid_cut_returns_valid_utf8():
    # 🎉 is 4 bytes; cutting to 3 bytes must yield a valid (possibly empty) string, not crash
    text = "🎉"
    out, cut = cap_text(text, max_bytes=3)
    out.encode("utf-8")  # must not raise
    assert cut is True
