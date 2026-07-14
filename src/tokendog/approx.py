from __future__ import annotations

_ENC = None  # None=unloaded, False=unavailable, else encoding object


def _encoding():
    global _ENC
    if _ENC is None:
        try:
            import tiktoken
            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENC = False
    return _ENC


def approx_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _encoding()
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)
