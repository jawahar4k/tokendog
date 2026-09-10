"""Context floor budget — MCP/skill/instruction sizing + used-vs-dead verdicts."""
import json
from pathlib import Path

import pytest

from tokendog.floor import _skill_used, floor_budget, skill_invocations


def _skill_used_cases():
    inv = {"git-workflow": 3, "infinite/api-patterns": 1, "plugin:foo": 2}
    assert _skill_used("infinite/git-workflow", inv) == 3     # leaf match
    assert _skill_used("infinite/api-patterns", inv) == 1     # exact
    assert _skill_used("foo", inv) == 2                       # plugin-qualified leaf
    assert _skill_used("nope", inv) == 0


def test_skill_used_matching():
    _skill_used_cases()


def _mk_home(tmp_path, skills, claude_md=None):
    root = tmp_path / ".claude" / "skills"
    for name, front, body in skills:
        d = root / name; d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")
    if claude_md:
        (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".claude" / "CLAUDE.md").write_text(claude_md, encoding="utf-8")


def _mk_transcripts(tmp_path, records):
    d = tmp_path / "tx" / "proj"; d.mkdir(parents=True)
    (d / "s.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return tmp_path / "tx"


def _skill_call(sid, rid, skill):
    return {"type": "assistant", "requestId": rid, "sessionId": sid, "cwd": "/w/proj",
            "timestamp": "2026-09-09T10:00:00Z",
            "message": {"content": [{"type": "tool_use", "id": rid, "name": "Skill",
                                     "input": {"skill": skill}}]}}


def test_skill_invocations_counts_by_name(tmp_path):
    tx = _mk_transcripts(tmp_path, [_skill_call("s", "r1", "git-workflow"),
                                    _skill_call("s", "r2", "git-workflow"),
                                    _skill_call("s", "r3", "dataviz")])
    inv = skill_invocations(tx)
    assert inv["git-workflow"] == 2 and inv["dataviz"] == 1


def test_never_invoked_skill_with_frontmatter_is_reclaim(tmp_path):
    _mk_home(tmp_path, [("dead-skill", "name: dead\n" + "x: y\n" * 50, "body")])
    tx = _mk_transcripts(tmp_path, [])   # never invoked
    d = floor_budget(tx, home=tmp_path)
    sk = next(i for i in d["items"] if i["item"] == "dead-skill")
    assert sk["kind"] == "skill" and sk["used"] is False
    assert sk["per_turn"] > 0            # frontmatter charged every turn
    assert sk["verdict"] == "reclaim"
    assert d["totals"]["reclaimable_per_turn"] >= sk["per_turn"]


def test_invoked_skill_is_kept(tmp_path):
    _mk_home(tmp_path, [("used-skill", "name: used\nshort: yes", "body")])
    tx = _mk_transcripts(tmp_path, [_skill_call("s", "r1", "used-skill")])
    d = floor_budget(tx, home=tmp_path)
    sk = next(i for i in d["items"] if i["item"] == "used-skill")
    assert sk["used"] is True and sk["verdict"] in ("keep", "trim")


def test_heavy_used_skill_is_trim(tmp_path):
    _mk_home(tmp_path, [("big", "name: big\n" + "line: value here\n" * 120, "body")])
    tx = _mk_transcripts(tmp_path, [_skill_call("s", "r1", "big")])
    d = floor_budget(tx, home=tmp_path)
    sk = next(i for i in d["items"] if i["item"] == "big")
    assert sk["per_turn"] >= 400 and sk["verdict"] == "trim"


def test_instruction_file_is_floor_but_kept(tmp_path):
    _mk_home(tmp_path, [], claude_md="Be concise.\n")
    d = floor_budget(_mk_transcripts(tmp_path, []), home=tmp_path)
    ins = next(i for i in d["items"] if i["kind"] == "instruction")
    assert ins["used"] is True and ins["verdict"] == "keep"


def test_reclaimable_totals_only_count_dead_items(tmp_path):
    _mk_home(tmp_path, [("dead", "name: d\n" + "k: v\n" * 40, "b"),
                        ("live", "name: l\nshort: y", "b")])
    tx = _mk_transcripts(tmp_path, [_skill_call("s", "r1", "live")])
    d = floor_budget(tx, home=tmp_path)
    dead = next(i for i in d["items"] if i["item"] == "dead")
    assert d["totals"]["reclaimable_per_turn"] == dead["per_turn"]   # live not counted
    assert d["totals"]["reclaim_items"] == 1
