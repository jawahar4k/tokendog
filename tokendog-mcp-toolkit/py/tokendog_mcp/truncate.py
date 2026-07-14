from __future__ import annotations

def cap_items(items, max_items: int = 100) -> tuple[list, bool]:
    items = list(items)
    if len(items) <= max_items:
        return items, False
    return items[:max_items], True

def cap_text(text: str, max_bytes: int = 50_000) -> tuple[str, bool]:
    if not text:
        return "", False
    b = text.encode("utf-8")
    if len(b) <= max_bytes:
        return text, False
    truncated = b[:max_bytes]
    while truncated and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]
    if truncated and (truncated[-1] & 0x80):
        truncated = truncated[:-1]
    return truncated.decode("utf-8"), True
