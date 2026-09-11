"""Synthetic transcript root + state dir for the README screenshot.

Run it, then:
  TOKENDOG_TRANSCRIPT_ROOT=docs/demo/projects TOKENDOG_HOME=docs/demo/home tokendog serve
and screenshot the Overview tab at 1206x1240. Nothing here is real.

Shapes copied from a real transcript; every name, id and number is invented.
"""
import json, random, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).parent
ROOT = OUT / "projects"; HOME = OUT / "home"
random.seed(7)
NOW = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)
PROJECTS = {"acme-api": 6, "storefront": 4, "data-pipeline": 5}
TOOLS = ["Read", "Bash", "Grep", "Edit", "Glob", "Write",
         "mcp__github__search_code", "mcp__github__get_pull_request",
         "mcp__postgres__query", "mcp__slack__post_message"]
MODELS = ["claude-opus-5", "claude-opus-5", "claude-sonnet-5"]

def usage(ctx, fresh, out, ttl1h=True):
    cw = max(0, int(fresh * random.uniform(0.6, 0.95)))
    return {"input_tokens": max(1, fresh - cw), "cache_creation_input_tokens": cw,
            "cache_read_input_tokens": ctx, "output_tokens": out,
            "service_tier": "standard", "inference_geo": "us",
            "cache_creation": {"ephemeral_1h_input_tokens": cw if ttl1h else 0,
                               "ephemeral_5m_input_tokens": 0 if ttl1h else cw}}

def session(project, day_offset, turns, peak, entry="cli"):
    sid = str(uuid.uuid4()); cwd = f"/home/dev/src/{project}"
    d = ROOT / ("-home-dev-src-" + project); d.mkdir(parents=True, exist_ok=True)
    t = NOW - timedelta(days=day_offset, hours=random.uniform(1, 9))
    ctx = random.randint(18000, 40000); model = random.choice(MODELS)
    recs = []; parent = None
    for i in range(turns):
        t += timedelta(seconds=random.choice([3, 4, 6, 9, 15, 40, 120]))
        if i and random.random() < 0.02 and ctx > 150000:
            ctx = random.randint(25000, 45000)  # a /compact
        fresh = random.randint(400, 9000); out = random.randint(60, 2500)
        ctx = min(peak, ctx + fresh)
        tool = random.choice(TOOLS)
        tid = "toolu_" + uuid.uuid4().hex[:20]
        a = {"parentUuid": parent, "isSidechain": False, "type": "assistant",
             "uuid": str(uuid.uuid4()), "timestamp": t.isoformat().replace("+00:00", "Z"),
             "userType": "external", "entrypoint": entry, "cwd": cwd, "sessionId": sid,
             "version": "2.1.0", "gitBranch": "main", "requestId": "req_" + uuid.uuid4().hex[:22],
             "message": {"model": model, "id": "msg_" + uuid.uuid4().hex[:22], "type": "message",
                         "role": "assistant", "stop_reason": "tool_use",
                         "content": [{"type": "tool_use", "id": tid, "name": tool,
                                      "input": {"command": "pytest -q"} if tool == "Bash" else {"file_path": "src/app.py"}}],
                         "usage": usage(ctx, fresh, out)}}
        recs.append(a); parent = a["uuid"]
        body = "\n".join(f"line {k}" for k in range(random.randint(5, 400)))
        u = {"parentUuid": parent, "isSidechain": False, "type": "user", "uuid": str(uuid.uuid4()),
             "timestamp": (t + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
             "userType": "external", "entrypoint": entry, "cwd": cwd, "sessionId": sid,
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": body}]}}
        recs.append(u); parent = u["uuid"]
    with (d / f"{sid}.jsonl").open("w") as f:
        for r in recs: f.write(json.dumps(r) + "\n")
    return sid

for project, n in PROJECTS.items():
    for k in range(n):
        turns = random.choice([40, 80, 120, 200, 320, 600])
        peak = random.choice([120000, 160000, 190000, 400000, 720000])
        session(project, random.randint(0, 6), turns, peak,
                entry="sdk-cli" if project == "data-pipeline" and k % 2 else "cli")
# one runaway session: the act-now item
session("acme-api", 0, 900, 960000)

HOME.mkdir(exist_ok=True); (HOME / "telemetry").mkdir(exist_ok=True)
tools = {}
for name in ["mcp__github__search_code", "mcp__github__get_pull_request", "mcp__github__create_issue",
             "mcp__github__list_commits", "mcp__postgres__query", "mcp__postgres__list_tables",
             "mcp__slack__post_message", "mcp__slack__list_channels",
             "mcp__jira__search_issues", "mcp__jira__get_issue", "mcp__jira__create_issue", "mcp__jira__transition",
             "mcp__figma__get_file", "mcp__figma__get_node", "mcp__figma__export"]:
    tools[name] = random.randint(15, 40)
(HOME / "surface.json").write_text(json.dumps({"captured_at": NOW.isoformat(), "tools": tools}, indent=1))
print("root:", ROOT); print("home:", HOME)
