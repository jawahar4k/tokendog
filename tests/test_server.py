import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from tokendog.server import DEFAULT_ADDRESS, PAGE, build_server, render_page

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _rec(ts, ctx):
    return {
        "type": "assistant",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "cwd": "/somewhere/projects/demo",
        "message": {"model": "test-model",
                    "usage": {"input_tokens": 0, "output_tokens": 10,
                              "cache_read_input_tokens": ctx,
                              "cache_creation_input_tokens": 0}},
    }


@pytest.fixture
def tree(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "s1.jsonl").write_text(
        json.dumps(_rec(NOW - timedelta(hours=1), 220_000)) + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def running(tree):
    """A real server on an ephemeral port — port 0 so parallel runs never clash."""
    httpd, cache = build_server(port=0, address=DEFAULT_ADDRESS, quiet=True,
                                transcript_root=tree)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://{DEFAULT_ADDRESS}:{httpd.server_port}", cache
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")


# --- the page ----------------------------------------------------------


def test_page_ships_with_the_package():
    assert PAGE.is_file(), "static/ledger.html must be installed alongside the module"


def test_page_has_a_single_data_placeholder():
    """Two would leave one un-substituted and the page would fail to parse."""
    assert PAGE.read_text(encoding="utf-8").count("__TOKENDOG_DATA__") == 1


def test_render_substitutes_the_placeholder():
    html = render_page({"headline": {"input": 7}}).decode("utf-8")
    assert "__TOKENDOG_DATA__" not in html
    assert '"input":7' in html


def test_render_escapes_a_closing_script_tag():
    """An unescaped </script> in the data would end the host element early."""
    html = render_page({"project": "</script><script>alert(1)</script>"}).decode("utf-8")
    body = html.split("const D = ", 1)[1]
    assert "</script><script>alert(1)" not in body.split(";\n", 1)[0]
    assert "<\\/script>" in body


def test_rendered_data_is_still_valid_json_after_escaping():
    blob = render_page({"project": "a</b>c"}).decode("utf-8")
    raw = blob.split("const D = ", 1)[1].split(";\n", 1)[0]
    assert json.loads(raw.replace("<\\/", "</"))["project"] == "a</b>c"


# --- routes ------------------------------------------------------------


def test_root_serves_html(running):
    base, _ = running
    status, ctype, body = _get(base + "/")
    assert status == 200
    assert "text/html" in ctype
    assert "<!doctype html>" in body.lower()
    assert "Context Ledger" in body


def test_data_route_serves_the_view_model(running):
    base, _ = running
    status, ctype, body = _get(base + "/data.json")
    assert status == 200
    assert "application/json" in ctype
    assert json.loads(body)["headline"]["input"] == 220_000


def test_unknown_path_is_404(running):
    base, _ = running
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base + "/../etc/passwd")
    assert e.value.code == 404


def test_responses_are_not_cached(running):
    """A cached page would show the previous ingest after a refresh."""
    base, _ = running
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        assert r.headers.get("Cache-Control") == "no-store"


# --- ingest caching ---------------------------------------------------


def test_ingest_happens_once_across_plain_loads(running):
    """A full ingest walks the whole tree; a page load must not pay for it."""
    base, cache = running
    _get(base + "/")
    first = cache.data
    _get(base + "/")
    assert cache.data is first, "plain loads must reuse the cached ingest"


def test_refresh_re_ingests(running):
    base, cache = running
    _get(base + "/data.json")
    first = cache.data
    _get(base + "/data.json?refresh=1")
    assert cache.data is not first


@pytest.mark.parametrize("q", ["refresh=0", "refresh=", "refresh=false", ""])
def test_falsey_refresh_values_do_not_re_ingest(running, q):
    base, cache = running
    _get(base + "/data.json")
    first = cache.data
    _get(base + "/data.json" + ("?" + q if q else ""))
    assert cache.data is first


def test_build_ms_is_reported(running):
    base, _ = running
    body = json.loads(_get(base + "/data.json")[2])
    assert isinstance(body["build_ms"], int)


# --- binding ----------------------------------------------------------


def test_default_bind_is_loopback():
    assert DEFAULT_ADDRESS == "127.0.0.1"


def test_no_web_framework_dependency():
    """The server is stdlib-only on purpose; a framework would be a new install."""
    import tokendog.server as srv
    source = srv.__file__
    text = open(source, encoding="utf-8").read()
    for framework in ("flask", "fastapi", "starlette", "aiohttp", "django", "bottle"):
        assert framework not in text.lower()


# --- range selection ---------------------------------------------------


def test_a_stray_range_falls_back_to_a_known_one():
    """An arbitrary span from a query string would walk the tree unbounded."""
    from tokendog.server import DEFAULT_RANGE, clamp_range
    assert clamp_range("today") == "today"
    assert clamp_range("30d") == "30d"
    assert clamp_range("nonsense") == DEFAULT_RANGE
    assert clamp_range(None) == DEFAULT_RANGE
    assert clamp_range("") == DEFAULT_RANGE


def test_a_bare_number_still_works_for_the_old_days_form():
    from tokendog.server import clamp_range
    assert clamp_range("7") == "7d"
    assert clamp_range("13") == "14d"
    assert clamp_range("99999") == "all"


def test_today_is_a_calendar_day_in_the_local_zone_not_the_last_24h():
    """A reader asking for 'today' means their day, not a rolling window."""
    from datetime import datetime, timezone
    from tokendog.server import window_for
    now = datetime(2026, 9, 8, 15, 30, tzinfo=timezone.utc).astimezone()
    since, until, days, label = window_for("today", now=now)
    assert (since.hour, since.minute, since.second) == (0, 0, 0)
    assert since.tzinfo == now.tzinfo, "local midnight, not UTC midnight"
    assert until == now and days == 1 and label == "Today"


def test_yesterday_ends_where_today_begins():
    from datetime import datetime, timezone
    from tokendog.server import window_for
    now = datetime(2026, 9, 8, 15, 30, tzinfo=timezone.utc).astimezone()
    y_since, y_until, _, _ = window_for("yesterday", now=now)
    t_since, _, _, _ = window_for("today", now=now)
    assert y_until == t_since, "no gap and no overlap between the two"
    assert (y_until - y_since).days == 1


def test_a_rolling_range_measures_back_from_now():
    from datetime import datetime, timezone
    from tokendog.server import window_for
    now = datetime(2026, 9, 8, 15, 30, tzinfo=timezone.utc).astimezone()
    since, until, days, _ = window_for("30d", now=now)
    assert days == 30
    assert (until - since).days == 30


def test_all_has_no_lower_bound():
    from tokendog.server import window_for
    since, _, _, label = window_for("all")
    assert since is None and label == "All"


def test_each_range_is_ingested_once_and_cached(running):
    base, cache = running
    _get(base + "/data.json?range=7d")
    first = cache.data
    _get(base + "/data.json?range=7d")
    assert cache.data is first
    _get(base + "/data.json?range=30d")
    assert cache.data is not first, "a different range is a different question"
    _get(base + "/data.json?range=7d")
    assert cache.data is first, "the earlier range is still cached"


def test_the_payload_reports_the_range_it_used(running):
    base, _ = running
    body = json.loads(_get(base + "/data.json?range=today")[2])
    assert body["range"] == "today"
    assert body["window_label"] == "Today"
    assert "7d" in body["ranges"]
    assert body["range_labels"]["yesterday"] == "Yesterday"


def test_refresh_clears_every_cached_range(running):
    base, cache = running
    _get(base + "/data.json?range=7d")
    _get(base + "/data.json?range=30d")
    first = cache._by_range["7d"]
    _get(base + "/data.json?range=7d&refresh=1")
    assert cache._by_range["7d"] is not first


# --- staleness ---------------------------------------------------------


def test_a_plain_reload_reuses_the_cache_while_nothing_has_changed(running):
    base, cache = running
    _get(base + "/data.json")
    first = cache.data
    _get(base + "/data.json")
    assert cache.data is first


def test_a_new_transcript_turn_invalidates_the_cache(tree, running):
    """Reloading after real activity must show it, without an explicit refresh."""
    base, cache = running
    _get(base + "/data.json")
    first = cache.data
    # a new file in the tree moves the fingerprint
    (tree / "proj" / "s2.jsonl").write_text(
        json.dumps(_rec(NOW - timedelta(hours=1), 150_000)) + "\n", encoding="utf-8")
    _get(base + "/data.json")
    assert cache.data is not first, "a changed tree must be re-ingested"


def test_the_fingerprint_is_a_stat_sweep_not_a_read(tree):
    """It runs on every page load, so it must not open the files."""
    import time as _t
    from tokendog.server import transcripts_fingerprint
    t0 = _t.monotonic()
    count, newest = transcripts_fingerprint(tree)
    assert count >= 1 and newest > 0
    assert _t.monotonic() - t0 < 1.0


def test_an_absent_tree_fingerprints_as_empty(tmp_path):
    from tokendog.server import transcripts_fingerprint
    assert transcripts_fingerprint(tmp_path / "nope") == (0, 0.0)


# --- the drilldown, in two shapes --------------------------------------


def test_session_page_and_json_are_the_same_data(tree, running):
    """Two readers, one drilldown: a page for a person, JSON for a machine."""
    base, _ = running
    status, ctype, html = _get(base + "/session/s1")
    assert status == 200 and "text/html" in ctype
    assert "__TOKENDOG_SESSION__" not in html, "placeholder must be substituted"
    status, ctype, body = _get(base + "/session/s1.json")
    assert status == 200 and "application/json" in ctype
    assert json.loads(body)["found"] is True


def test_the_page_carries_a_link_to_its_own_json(tree, running):
    base, _ = running
    html = _get(base + "/session/s1")[2]
    assert 'id="jsonlink"' in html


def test_an_unknown_session_404s_in_both_shapes(running):
    base, _ = running
    for path in ("/session/nope", "/session/nope.json"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(base + path)
        assert e.value.code == 404


def test_the_session_page_ships_with_the_package():
    from tokendog.server import SESSION_PAGE
    assert SESSION_PAGE.is_file()
    assert SESSION_PAGE.read_text(encoding="utf-8").count("__TOKENDOG_SESSION__") == 1


def test_render_page_can_target_either_template():
    from tokendog.server import SESSION_PAGE, render_page
    out = render_page({"found": False}, SESSION_PAGE, "__TOKENDOG_SESSION__").decode("utf-8")
    assert '"found":false' in out
