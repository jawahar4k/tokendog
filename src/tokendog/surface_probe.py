from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from typing import Any

from .approx import approx_tokens

# Measuring what a connector costs requires asking it what tools it has, and
# that means starting it. This module is the ONLY place in tokendog that
# launches anything, and it is reached only from `tokendog surface --refresh`.
# Everything else — every report, every detector — reads files.
#
# WHY IT CANNOT BE AVOIDED. A tool definition's size is a property of the
# request, and the transcript keeps the response. Nothing on disk holds the
# schemas either (checked: the plugin cache carries source, not inventories).
# So either the sizes are measured by asking, or they are guessed. Guessing the
# size of the thing whose size is the entire point would be worthless.
#
# WHAT IT COSTS THE READER. Starting a server can prompt for credentials, sit
# waiting on a network, or fail outright. So: a hard per-connector timeout,
# every failure caught and reported rather than raised, and the result cached to
# disk so the price is paid once rather than on every report. A connector that
# cannot be reached is recorded as unreachable — which is itself worth knowing,
# since an unreachable server is still costing you its definitions.
#
# WHAT IS COUNTED. Name + description + input schema, as JSON — the shape a
# tool is presented to the model in. It is a tiktoken approximation, not the
# provider's own count, so treat it as the right order of magnitude for
# deciding what to remove rather than as a billing figure.

DEFAULT_TIMEOUT = 20.0


def tool_tokens(name: str, description: Any, schema: Any) -> int:
    """Approximate tokens for one tool definition as the model receives it."""
    payload = {"name": name, "description": description or "",
               "inputSchema": schema if schema is not None else {}}
    try:
        text = json.dumps(payload, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return approx_tokens(text)


def _env_for(cfg: dict) -> dict:
    """The server's environment: inherited, then overlaid with its own.

    Inheriting matters — a server launched with only its declared env loses
    PATH and HOME and fails for reasons that have nothing to do with tokendog.
    """
    env = dict(os.environ)
    extra = cfg.get("env")
    if isinstance(extra, dict):
        env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


async def _list_stdio(cfg: dict) -> list:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = cfg.get("command")
    if not command:
        raise ValueError("no command")
    params = StdioServerParameters(
        command=str(command),
        args=[str(a) for a in (cfg.get("args") or [])],
        env=_env_for(cfg),
        cwd=cfg.get("cwd") or None,
    )
    # A server's own stderr is captured rather than inherited. Servers announce
    # startup warnings, stale-state notices and whole stack traces on stderr,
    # and letting that through interleaves pages of another program's output
    # into this report. What is captured is kept: the tail of it is by far the
    # most useful explanation when a server fails to start.
    # A real file, not StringIO: the client redirects the child's stderr at the
    # file-descriptor level, so the sink has to have a fileno.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as buf:
        try:
            async with stdio_client(params, errlog=buf) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return list((await session.list_tools()).tools)
        except BaseException as exc:
            try:
                buf.seek(0)
                setattr(exc, "_tokendog_stderr", _tail(buf.read()))
            except (OSError, ValueError):
                pass
            raise


def _tail(text: str, lines: int = 3, width: int = 240) -> str:
    """The last few meaningful lines of a server's stderr."""
    kept = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(kept[-lines:])[:width]


async def _list_http(cfg: dict) -> list:
    from mcp import ClientSession
    url = cfg.get("url") or cfg.get("endpoint")
    if not url:
        raise ValueError("no url")
    kind = str(cfg.get("type") or "").lower()
    if kind == "sse":
        from mcp.client.sse import sse_client as client
    else:
        import mcp.client.streamable_http as sh
        # The name changed across SDK releases; take whichever this one has
        # rather than pinning a version for one symbol.
        client = None
        for attr in ("streamable_http_client", "streamablehttp_client"):
            client = getattr(sh, attr, None)
            if client:
                break
        if client is None:
            raise ImportError("no streamable-http client in the installed mcp SDK")
    async with client(str(url)) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            return list((await session.list_tools()).tools)


def _reason(exc: BaseException, depth: int = 0) -> str:
    """A cause a reader can act on.

    An MCP client failure arrives wrapped in an ExceptionGroup from the task
    group that ran it, and the group's own message ("1 sub-exception") names
    nothing — so the wrapper is unwrapped to the first real cause.

    A missing command is called out specially because it is the common case and
    the least obvious: this process inherits the PATH of whatever shell started
    it, and a launcher script found in a login shell is often absent from a
    non-interactive one.
    """
    captured = getattr(exc, "_tokendog_stderr", "")
    inner = getattr(exc, "exceptions", None)
    if inner and depth < 4:
        nested = _reason(inner[0], depth + 1)
        return f"{nested} [{captured}]" if captured and captured not in nested else nested
    if isinstance(exc, FileNotFoundError):
        missing = getattr(exc, "filename", None) or str(exc)
        return (f"command not found on PATH: {missing} — the probe inherits the PATH of "
                "the shell that ran it")
    base = f"{type(exc).__name__}: {exc}"[:200]
    return f"{base} [{captured}]" if captured else base


async def _probe_one(name: str, cfg: dict, timeout: float) -> dict:
    started = time.monotonic()
    kind = str(cfg.get("type") or ("stdio" if cfg.get("command") else "http")).lower()
    try:
        lister = _list_stdio if kind == "stdio" else _list_http
        tools = await asyncio.wait_for(lister(cfg), timeout=timeout)
    except asyncio.TimeoutError:
        return {"connector": name, "ok": False, "error": f"timed out after {timeout:.0f}s",
                "tools": {}, "ms": int((time.monotonic() - started) * 1000)}
    except BaseException as exc:  # a server can fail in any way at all
        return {"connector": name, "ok": False, "error": _reason(exc),
                "tools": {}, "ms": int((time.monotonic() - started) * 1000)}
    sized = {
        f"mcp__{name}__{t.name}": tool_tokens(
            t.name, getattr(t, "description", None), getattr(t, "inputSchema", None))
        for t in tools
    }
    return {"connector": name, "ok": True, "error": None, "tools": sized,
            "ms": int((time.monotonic() - started) * 1000)}


async def _probe_all(configs: dict, timeout: float) -> list[dict]:
    # Sequential, not concurrent: several servers starting at once can each
    # want the terminal for a credential prompt, and interleaved prompts are
    # unanswerable. Slower and comprehensible beats faster and stuck.
    results = []
    for name, cfg in configs.items():
        results.append(await _probe_one(name, cfg or {}, timeout))
    return results


def probe_connectors(configs: dict, *, timeout: float = DEFAULT_TIMEOUT,
                     on_progress=None) -> tuple[dict, list[dict]]:
    """Ask each connector for its tools. Returns (sizes, per-connector status).

    `configs` maps connector name -> its launch config. Never raises: a
    connector that cannot be reached comes back with `ok: False` and a reason.
    """
    if not configs:
        return {}, []
    if on_progress:
        on_progress(list(configs))
    try:
        results = asyncio.run(_probe_all(configs, timeout))
    except BaseException as exc:
        return {}, [{"connector": "*", "ok": False,
                     "error": f"{type(exc).__name__}: {exc}"[:200],
                     "tools": {}, "ms": 0}]
    sizes: dict[str, int] = {}
    for r in results:
        sizes.update(r["tools"])
    return sizes, results


def format_probe(results: list[dict]) -> str:
    lines = []
    for r in sorted(results, key=lambda x: (x["ok"], x["connector"])):
        if r["ok"]:
            lines.append(f"  {r['connector']}: {len(r['tools'])} tool(s), "
                         f"{sum(r['tools'].values()):,} tokens ({r['ms']} ms)")
        else:
            lines.append(f"  {r['connector']}: unreachable — {r['error']}")
    return "\n".join(lines)
