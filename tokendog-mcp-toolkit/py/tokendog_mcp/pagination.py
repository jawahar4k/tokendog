from __future__ import annotations


def paginate(items, page: int = 1, page_size: int = 50) -> dict:
    items = list(items)
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    total = len(items)
    start = (page - 1) * page_size
    chunk = items[start:start + page_size]
    has_more = start + page_size < total
    return {
        "items": chunk,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": has_more,
        "next_page": page + 1 if has_more else None,
    }
