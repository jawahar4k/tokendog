from __future__ import annotations


def truncate_text(text: str, max_lines: int = 200, max_bytes: int = 50_000) -> tuple[str, bool]:
    if not text:
        return "", False
    truncated = False
    b = text.encode("utf-8")
    if len(b) > max_bytes:
        text = b[:max_bytes].decode("utf-8", "ignore")
        truncated = True
    lines = text.splitlines()
    if len(lines) > max_lines:
        half = max(1, max_lines // 2)
        head, tail = lines[:half], lines[-half:]
        omitted = len(lines) - len(head) - len(tail)
        text = "\n".join(head + [f"... [tokendog: {omitted} lines truncated] ..."] + tail)
        truncated = True
    return text, truncated
