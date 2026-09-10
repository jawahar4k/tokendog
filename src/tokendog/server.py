from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .transcripts import transcript_root
from .views import ledger_data

# A read-only local dashboard over the same figures the CLI reports.
#
# Deliberately built on `http.server` from the standard library rather than a
# web framework. The whole surface is two GET routes serving one HTML file and
# one JSON document to a single reader on loopback; a framework dependency
# would be the first thing in this package not carrying its weight, and it
# would have to be installed on every machine that only ever runs `bands`.
#
# Binding: loopback by default, and there is no authentication. The page renders
# transcript-derived figures — project names, session ids, token counts — so
# exposing it on a routable interface publishes those to anyone who can reach
# the port. `--address` exists for the reverse-proxy case and is opt-in.
#
# Freshness: a full ingest walks the whole transcript tree, which is seconds to
# minutes on a real machine, so it happens once at startup and is cached. The
# page's Refresh control re-runs it explicitly. Nothing recomputes on a plain
# page load, because a dashboard that costs a minute to reload is one nobody
# reloads.

DEFAULT_PORT = 4320
DEFAULT_ADDRESS = "127.0.0.1"

STATIC = Path(__file__).with_name("static")
PAGE = STATIC / "ledger.html"
SESSION_PAGE = STATIC / "session.html"


def render_page(data: dict, page=None, token: str = "__TOKENDOG_DATA__") -> bytes:
    """Inline the view-model into the page, so a load needs no second request."""
    html = (page or PAGE).read_text(encoding="utf-8")
    # </script> inside JSON would close the host <script> element early; the
    # escape is on the '/' so the JSON still parses to the identical string.
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return html.replace(token, blob).encode("utf-8")


# The ranges the page offers, in order. Two kinds, deliberately:
#   CALENDAR — today, yesterday: bounded by local midnight, because "today" is
#              a day in the reader's timezone, not the last 24 hours.
#   ROLLING  — 7d and up: a span measured back from now.
# A fixed list rather than a free-form span: an arbitrary `days` from a query
# string would let one request walk the whole tree for as long as it liked.
RANGES = ("today", "yesterday", "7d", "14d", "30d", "90d", "all")
DEFAULT_RANGE = "7d"
RANGE_LABEL = {"today": "Today", "yesterday": "Yesterday", "7d": "7d",
               "14d": "14d", "30d": "30d", "90d": "90d", "all": "All"}


def clamp_range(value) -> str:
    """A known range token, so a stray query string cannot pick its own span."""
    token = str(value or "").strip().lower()
    if token in RANGES:
        return token
    # Accept a bare number for the old `?days=` form.
    digits = "".join(ch for ch in token if ch.isdigit())
    if digits:
        want = int(digits)
        rolling = [(int(r[:-1]), r) for r in RANGES if r.endswith("d")]
        return min(rolling, key=lambda r: abs(r[0] - want))[1] if want < 365 else "all"
    return DEFAULT_RANGE


def window_for(token: str, now=None):
    """(since, until, days, label) for a range token, in the LOCAL timezone.

    Local matters for the calendar ranges: midnight is the reader's midnight.
    `astimezone()` with no argument uses the machine's own zone, which for a
    dashboard you run yourself is exactly the right one.
    """
    now = (now or datetime.now(timezone.utc)).astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if token == "today":
        return midnight, now, 1, RANGE_LABEL[token]
    if token == "yesterday":
        return midnight - timedelta(days=1), midnight, 1, RANGE_LABEL[token]
    if token == "all":
        return None, now, 3650, RANGE_LABEL[token]
    days = int(token[:-1])
    return now - timedelta(days=days), now, days, RANGE_LABEL[token]


def transcripts_fingerprint(root=None) -> tuple[int, float]:
    """(file count, newest mtime) across the transcript tree.

    Milliseconds to compute — it stats, it does not read — so it can run on
    every page load. That is what lets a plain reload show current numbers
    without paying a full ingest each time.
    """
    base = Path(root).expanduser() if root else transcript_root()
    if not base.exists():
        return (0, 0.0)
    count = 0
    newest = 0.0
    for f in base.rglob("*.jsonl"):
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        count += 1
        if m > newest:
            newest = m
    return (count, newest)


class _Cache:
    """Holds each ingest so a page load is instant, and notices when it is stale.

    Keyed by range token: switching range is a new question, and re-walking the
    tree for a range already looked at would make the selector feel broken.

    A cached answer is reused only while the transcript tree is UNCHANGED. The
    check is a stat sweep, not a read, so it costs milliseconds — which means a
    plain reload shows current numbers when something has happened and stays
    instant when nothing has. Explicit refresh still forces the work.
    """

    def __init__(self, **kw):
        self._kw = kw
        # The root the server was started with. The drilldown route needs it:
        # without it a custom root was silently ignored and the drilldown read
        # the default location instead.
        self.transcript_root = kw.get("transcript_root")
        self._by_range: dict[str, dict] = {}
        self._ms: dict[str, int] = {}
        self._stamp: dict[str, tuple] = {}
        # Outcomes is cached separately and per (range, gh): it shells out to git
        # (and optionally gh) per repo, which is far heavier than a transcript
        # sweep, so it is computed only when the Outcomes tab actually asks —
        # never on a plain dashboard load.
        self._outcomes: dict[tuple, dict] = {}
        self._errors_by_range: dict[str, dict] = {}
        self._cost_by_range: dict[str, dict] = {}
        self._pipelines_by_range: dict[str, dict] = {}
        self._discovery_by_range: dict[str, dict] = {}
        self._floor_by_range: dict[str, dict] = {}
        self.data: dict | None = None
        self.built_ms: int = 0

    def cost(self, token: str, *, refresh: bool = False) -> dict:
        from .report import cost_summary
        token = clamp_range(token or DEFAULT_RANGE)
        fp = self._fingerprint()
        if refresh or self._stamp.get(("cost", token)) != fp:
            since, until, _days, _label = window_for(token)
            iso = lambda d: d.isoformat() if d else None
            start = time.monotonic()
            by_model = cost_summary(group_by="model", since=iso(since), until=iso(until))
            by_runtime = cost_summary(group_by="runtime", since=iso(since), until=iso(until))
            by_tool = cost_summary(group_by="tool", since=iso(since), until=iso(until))
            data = {"by_model": by_model["rows"], "by_runtime": by_runtime["rows"],
                    "by_tool": [r for r in by_tool["rows"]
                                if r["key"] != "(none)" and r["tool_payload_tokens"] > 0],
                    "note": by_model.get("note", ""),
                    "build_ms": int((time.monotonic() - start) * 1000), "range": token}
            self._cost_by_range[token] = data
            self._stamp[("cost", token)] = fp
        return self._cost_by_range[token]

    def floor(self, token: str, *, refresh: bool = False) -> dict:
        from .floor import floor_budget
        from .window import Window
        token = clamp_range(token or DEFAULT_RANGE)
        fp = self._fingerprint()
        if refresh or self._stamp.get(("floor", token)) != fp:
            since, until, _days, _label = window_for(token)
            win = Window(since=since, until=until) if (since or until) else None
            start = time.monotonic()
            data = floor_budget(self.transcript_root, window=win)
            data["build_ms"] = int((time.monotonic() - start) * 1000)
            data["range"] = token
            self._floor_by_range[token] = data
            self._stamp[("floor", token)] = fp
        return self._floor_by_range[token]

    def discovery(self, token: str, *, refresh: bool = False) -> dict:
        from .discovery import discovery_report
        from .window import Window
        token = clamp_range(token or DEFAULT_RANGE)
        fp = self._fingerprint()
        if refresh or self._stamp.get(("discovery", token)) != fp:
            since, until, _days, _label = window_for(token)
            win = Window(since=since, until=until) if (since or until) else None
            start = time.monotonic()
            data = discovery_report(self.transcript_root, window=win)
            data["build_ms"] = int((time.monotonic() - start) * 1000)
            data["range"] = token
            self._discovery_by_range[token] = data
            self._stamp[("discovery", token)] = fp
        return self._discovery_by_range[token]

    def pipelines(self, token: str, *, refresh: bool = False) -> dict:
        from .glitch_runs import pipeline_costs
        from .window import Window
        token = clamp_range(token or DEFAULT_RANGE)
        fp = self._fingerprint()
        if refresh or self._stamp.get(("pipelines", token)) != fp:
            since, until, _days, _label = window_for(token)
            win = Window(since=since, until=until) if (since or until) else None
            start = time.monotonic()
            data = pipeline_costs(self.transcript_root, window=win)
            data["build_ms"] = int((time.monotonic() - start) * 1000)
            data["range"] = token
            self._pipelines_by_range[token] = data
            self._stamp[("pipelines", token)] = fp
        return self._pipelines_by_range[token]

    def errors(self, token: str, *, refresh: bool = False) -> dict:
        from .errors import tool_errors
        from .window import Window
        token = clamp_range(token or DEFAULT_RANGE)
        fp = self._fingerprint()
        if refresh or self._stamp.get(("errors", token)) != fp:
            since, until, _days, _label = window_for(token)
            win = Window(since=since, until=until) if (since or until) else None
            start = time.monotonic()
            data = tool_errors(self.transcript_root, window=win)
            data["build_ms"] = int((time.monotonic() - start) * 1000)
            data["range"] = token
            self._errors_by_range[token] = data
            self._stamp[("errors", token)] = fp
        return self._errors_by_range[token]

    def outcomes(self, token: str, *, use_gh: bool = False, refresh: bool = False) -> dict:
        from .outcomes import outcomes_data
        from .window import Window
        token = clamp_range(token or DEFAULT_RANGE)
        key = (token, use_gh)
        fp = self._fingerprint()
        if refresh or self._stamp.get(("outcomes", key)) != fp:
            since, until, _days, _label = window_for(token)
            win = Window(since=since, until=until) if (since or until) else None
            start = time.monotonic()
            data = outcomes_data(self.transcript_root, window=win, use_gh=use_gh)
            data["build_ms"] = int((time.monotonic() - start) * 1000)
            data["range"] = token
            self._outcomes[key] = data
            self._stamp[("outcomes", key)] = fp
        return self._outcomes[key]

    def _fingerprint(self) -> tuple:
        return transcripts_fingerprint(self._kw.get("transcript_root"))

    def get(self, refresh: bool = False, token: str | None = None) -> dict:
        token = clamp_range(token or self._kw.get("range") or DEFAULT_RANGE)
        fp = self._fingerprint()
        if refresh:
            self._by_range.clear(); self._ms.clear(); self._stamp.clear()
        stale = self._stamp.get(token) != fp
        if token not in self._by_range or stale:
            since, until, days, label = window_for(token)
            kw = {k: v for k, v in self._kw.items() if k != "range"}
            kw.update(window_days=days, since=since, until=until, window_label=label)
            start = time.monotonic()
            self._by_range[token] = ledger_data(**kw)
            self._ms[token] = int((time.monotonic() - start) * 1000)
            self._stamp[token] = fp
        data = self._by_range[token]
        self.data = data
        self.built_ms = self._ms[token]
        data["build_ms"] = self._ms[token]
        data["ranges"] = list(RANGES)
        data["range"] = token
        data["range_labels"] = dict(RANGE_LABEL)
        return data


def _handler(cache: _Cache, quiet: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tokendog"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: A003 - base-class name
            if not quiet:
                super().log_message(fmt, *args)

        def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # The page is generated per request from local data; a cached copy
            # would show yesterday's numbers after a refresh.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - base-class name
            url = urlparse(self.path)
            query = parse_qs(url.query)
            refresh = query.get("refresh", ["0"])[0] not in ("0", "", "false")
            token = clamp_range(query.get("range", query.get("days", [None]))[0])
            try:
                if url.path in ("/", "/index.html"):
                    self._send(render_page(cache.get(refresh, token)),
                               "text/html; charset=utf-8")
                elif url.path == "/data.json":
                    body = json.dumps(cache.get(refresh, token), indent=1).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                elif url.path == "/floor.json":
                    body = json.dumps(cache.floor(token, refresh=refresh),
                                      indent=1).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                elif url.path == "/discovery.json":
                    body = json.dumps(cache.discovery(token, refresh=refresh),
                                      indent=1).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                elif url.path == "/pipelines.json":
                    body = json.dumps(cache.pipelines(token, refresh=refresh),
                                      indent=1).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                elif url.path == "/cost.json":
                    body = json.dumps(cache.cost(token, refresh=refresh),
                                      indent=1).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                elif url.path == "/errors.json":
                    body = json.dumps(cache.errors(token, refresh=refresh),
                                      indent=1).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                elif url.path == "/outcomes.json":
                    # Lazy: only the Outcomes tab hits this, so git runs on demand
                    # rather than on every dashboard load.
                    use_gh = query.get("gh", ["0"])[0] not in ("0", "", "false")
                    body = json.dumps(cache.outcomes(token, use_gh=use_gh, refresh=refresh),
                                      indent=1).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                elif url.path.startswith("/session/"):
                    # Two shapes of the same drilldown, because they have
                    # different readers: a page for a person, JSON for anything
                    # that wants to analyse it. `.json` picks the second.
                    # Turn-by-turn drilldown for one session, as JSON so it can be
                    # read here or handed to something else to analyse. Not cached:
                    # it reads one file, which is fast, and a stale drilldown of the
                    # session you are watching would be worse than useless.
                    raw = url.path[len("/session/"):]
                    as_json = raw.endswith(".json")
                    ident = raw.removesuffix(".json")
                    from .session_detail import session_detail
                    from .window import Window
                    # Carry the dashboard's selected range in as a window, so a
                    # session opened from a Today view marks the Today turns and
                    # the drilldown answers the same question the reader was
                    # already asking. The range round-trips in the URL for the
                    # back-link too.
                    since, until, _days, _wlabel = window_for(token)
                    win = (Window(since=since, until=until)
                           if (since or until) else None)
                    detail = session_detail(
                        ident, root=cache.transcript_root,
                        top=int(query.get("top", ["25"])[0] or 25),
                        window=win)
                    detail["range"] = token
                    detail["ranges"] = list(RANGES)
                    detail["range_labels"] = dict(RANGE_LABEL)
                    status = 200 if detail.get("found") else 404
                    if as_json:
                        self._send(json.dumps(detail, indent=1).encode("utf-8"),
                                   "application/json; charset=utf-8", status)
                    else:
                        self._send(render_page(detail, SESSION_PAGE,
                                               "__TOKENDOG_SESSION__"),
                                   "text/html; charset=utf-8", status)
                else:
                    self._send(b"not found\n", "text/plain; charset=utf-8", 404)
            except BrokenPipeError:
                pass  # reader navigated away mid-response
            except Exception as exc:  # a stack trace to the terminal, a line to the page
                self.log_error("%s", exc)
                self._send(f"error: {exc}\n".encode(), "text/plain; charset=utf-8", 500)

        def do_HEAD(self) -> None:  # noqa: N802
            self._send(b"", "text/plain; charset=utf-8")

    return Handler


def build_server(*, port: int = DEFAULT_PORT, address: str = DEFAULT_ADDRESS,
                 quiet: bool = False, **view_kw) -> tuple[ThreadingHTTPServer, _Cache]:
    """Create the server without starting it — the shape tests need."""
    cache = _Cache(**view_kw)
    httpd = ThreadingHTTPServer((address, port), _handler(cache, quiet))
    httpd.daemon_threads = True
    return httpd, cache


def serve(*, port: int = DEFAULT_PORT, address: str = DEFAULT_ADDRESS,
          project: str | None = None, transcript_root=None,
          quiet: bool = False) -> int:
    httpd, cache = build_server(port=port, address=address, quiet=quiet,
                                project=project, transcript_root=transcript_root)
    scope = f" · project={project}" if project else ""
    print(f"TokenDog dashboard  http://{address}:{httpd.server_port}{scope}")
    print("Reading transcripts…", end="", flush=True)
    data = cache.get()
    print(f" {data['headline']['turns']:,} turns in the last "
          f"{data['window_days']} days ({cache.built_ms} ms)")
    if address not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: bound to {address} — this page is unauthenticated "
              "and shows your project names, session ids and token counts.")
    print("Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
    return 0
