from tokendog_mcp.pagination import paginate

def test_first_page_has_more():
    r = paginate(list(range(120)), page=1, page_size=50)
    assert r["items"] == list(range(50))
    assert r["total"] == 120 and r["has_more"] is True and r["next_page"] == 2

def test_last_page_no_more():
    r = paginate(list(range(120)), page=3, page_size=50)
    assert r["items"] == list(range(100, 120))
    assert r["has_more"] is False and r["next_page"] is None

def test_clamps_bad_args():
    r = paginate([1, 2, 3], page=0, page_size=0)
    assert r["page"] == 1 and r["page_size"] == 1 and r["items"] == [1]
