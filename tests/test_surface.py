import json

import pytest

from tokendog.event import RUNTIME_CLAUDE, SOURCE_HOOK, SOURCE_TRANSCRIPT, TokenEvent
from tokendog.surface import (
    LOW_USE_PER_1K_TURNS,
    local_surface,
    parse_mcp_tool,
    read_enablement,
    surface_summary,
)

TS = "2026-09-07T10:00:00Z"


def _turn(ctx=100_000, *, tools=None, project="demo", transcript="t1"):
    return TokenEvent(
        ts=TS, session_id=transcript, transcript_id=transcript, runtime=RUNTIME_CLAUDE,
        event="assistant-turn", source=SOURCE_TRANSCRIPT, cache_read_tokens=ctx,
        output_tokens=10, project=project, tools=tools)


def _hook(tool, project="demo"):
    return TokenEvent(ts=TS, session_id="t1", runtime=RUNTIME_CLAUDE, event="tool",
                      source=SOURCE_HOOK, tool=tool, project=project,
                      tool_payload_tokens=10)


def _home(tmp_path, *, root=None, settings=None):
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude.json").write_text(json.dumps(root or {}), encoding="utf-8")
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps(settings or {}), encoding="utf-8")
    return tmp_path


# --- tool-name parsing -------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("mcp__glitch__run_pipeline", ("glitch", "run_pipeline")),
    # a connector routinely contains single underscores...
    ("mcp__plugin_context7_context7__query-docs",
     ("plugin_context7_context7", "query-docs")),
    # ...and a tool routinely contains hyphens
    ("mcp__foo__resolve-library-id", ("foo", "resolve-library-id")),
])
def test_mcp_tool_names_split_into_connector_and_tool(name, expected):
    assert parse_mcp_tool(name) == expected


@pytest.mark.parametrize("name", ["Bash", "Read", "mcp__", "mcp__only", "", None, 7])
def test_non_mcp_names_are_not_connectors(name):
    assert parse_mcp_tool(name) is None


def test_a_tool_name_containing_a_double_underscore_keeps_it():
    """Split exactly twice, so the tail stays intact whatever is in it."""
    assert parse_mcp_tool("mcp__srv__a__b") == ("srv", "a__b")


# --- enablement --------------------------------------------------------


def test_global_servers_are_in_scope_everywhere(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"github": {}}})
    c = read_enablement(h)["connectors"]["github"]
    assert (c["scope"], c["enabled"]) == ("global", True)


def test_project_servers_carry_their_project(tmp_path):
    h = _home(tmp_path, root={"projects": {
        "/x/invest7": {"mcpServers": {"kite": {}}}}})
    c = read_enablement(h)["connectors"]["kite"]
    assert c["scope"] == "project"
    assert c["projects"] == ["invest7"]


def test_a_disabled_project_server_is_reported_disabled(tmp_path):
    h = _home(tmp_path, root={"projects": {"/x/p": {
        "mcpServers": {"srv": {}}, "disabledMcpjsonServers": ["srv"]}}})
    assert read_enablement(h)["connectors"]["srv"]["enabled"] is False


def test_plugin_servers_are_discovered_from_the_plugin_cache(tmp_path):
    h = _home(tmp_path, settings={"enabledPlugins": {"ctx@market": True}})
    d = h / ".claude" / "plugins" / "cache" / "market" / "ctx" / "1.0"
    d.mkdir(parents=True)
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {"ctx": {}}}), encoding="utf-8")
    c = read_enablement(h)["connectors"]["plugin_ctx_ctx"]
    assert (c["scope"], c["enabled"]) == ("plugin", True)


def test_a_plugin_never_switched_on_is_reported_disabled(tmp_path):
    h = _home(tmp_path, settings={"enabledPlugins": {}})
    d = h / ".claude" / "plugins" / "cache" / "market" / "ctx" / "1.0"
    d.mkdir(parents=True)
    (d / ".mcp.json").write_text(json.dumps({"mcpServers": {"ctx": {}}}), encoding="utf-8")
    assert read_enablement(h)["connectors"]["plugin_ctx_ctx"]["enabled"] is False


def test_a_server_declared_as_a_directory_entry_is_found(tmp_path):
    """Some plugins ship `mcpServers/<name>.json` rather than one `.mcp.json`."""
    h = _home(tmp_path, settings={"enabledPlugins": {"p@m": True}})
    d = h / ".claude" / "plugins" / "cache" / "m" / "p" / "1.0" / "mcpServers"
    d.mkdir(parents=True)
    (d / "thing-cost.json").write_text("{}", encoding="utf-8")
    assert "plugin_p_thing-cost" in read_enablement(h)["connectors"]


def test_missing_config_is_not_an_error(tmp_path):
    assert read_enablement(tmp_path) == {"connectors": {}, "plugins": {}}


def test_unreadable_config_is_not_an_error(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text("{ not json", encoding="utf-8")
    assert read_enablement(tmp_path)["connectors"] == {}


# --- local surface -----------------------------------------------------


def test_skills_are_found_when_nested(tmp_path):
    """A bundle directory holds many skills; one level deep would miss them."""
    d = tmp_path / ".claude" / "skills" / "bundle" / "inner"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: inner\n---\nbody text", encoding="utf-8")
    names = [s["name"] for s in local_surface(tmp_path)["skills"]]
    assert names == ["bundle/inner"]


def test_a_skill_is_priced_in_two_parts(tmp_path):
    """Frontmatter rides in every prompt; the body only on invoke."""
    d = tmp_path / ".claude" / "skills" / "s"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: s\ndescription: " + ("x " * 50) + "\n---\n" + ("y " * 400),
        encoding="utf-8")
    sk = local_surface(tmp_path)["skills"][0]
    assert sk["always_on_tokens"] > 0
    assert sk["on_demand_tokens"] > sk["always_on_tokens"]


def test_a_skill_without_frontmatter_has_no_always_on_cost(tmp_path):
    d = tmp_path / ".claude" / "skills" / "s"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("just a body", encoding="utf-8")
    assert local_surface(tmp_path)["skills"][0]["always_on_tokens"] == 0


def test_an_empty_home_has_an_empty_local_surface(tmp_path):
    out = local_surface(tmp_path)
    assert out == {"skills": [], "commands": [], "instructions": []}


# --- grouping and verdicts --------------------------------------------


def test_tools_are_grouped_under_their_connector(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"glitch": {}}})
    events = [
        _turn(tools=["mcp__glitch__run_pipeline"]),
        _turn(tools=["mcp__glitch__run_pipeline", "mcp__glitch__list_runs"]),
    ]
    c = surface_summary(events, home=h)["connectors"][0]
    assert c["connector"] == "glitch"
    assert c["tools_called"] == 2
    assert c["calls"] == 3
    assert c["tools"][0] == {"tool": "run_pipeline", "calls": 2}


def test_an_enabled_but_never_called_connector_is_a_disable_candidate(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"github": {}}})
    d = surface_summary([_turn(), _turn()], home=h)
    c = d["connectors"][0]
    assert c["connector"] == "github"
    assert c["severity"] == "unused"
    assert c["turns_resident"] == 2
    assert d["totals"]["unused"] == 1


def test_zero_residency_is_reported_as_no_data_not_as_useless(tmp_path):
    """Recommending a disable on no evidence is the one unforgivable output."""
    h = _home(tmp_path, root={"projects": {"/x/other": {"mcpServers": {"kite": {}}}}})
    c = surface_summary([_turn(project="demo")], home=h)["connectors"][0]
    assert c["turns_resident"] == 0
    assert c["severity"] == "nodata"
    assert "disable" not in c["action"].lower()


def test_a_rarely_called_connector_is_flagged_for_review(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"srv": {}}})
    events = [_turn(tools=["mcp__srv__t"])] + [_turn() for _ in range(4000)]
    c = surface_summary(events, home=h)["connectors"][0]
    assert c["severity"] == "low"
    assert c["calls_per_1k_turns"] < LOW_USE_PER_1K_TURNS


def test_a_well_used_connector_is_left_alone(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"srv": {}}})
    events = [_turn(tools=["mcp__srv__t"]) for _ in range(20)]
    assert surface_summary(events, home=h)["connectors"][0]["severity"] == "used"


def test_project_scoped_residency_counts_only_that_project(tmp_path):
    h = _home(tmp_path, root={"projects": {"/x/alpha": {"mcpServers": {"srv": {}}}}})
    events = [_turn(project="alpha"), _turn(project="beta"), _turn(project="beta")]
    assert surface_summary(events, home=h)["connectors"][0]["turns_resident"] == 1


def test_disable_candidates_are_listed_before_healthy_ones(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"unused": {}, "busy": {}}})
    events = [_turn(tools=["mcp__busy__t"]) for _ in range(10)]
    order = [c["connector"] for c in surface_summary(events, home=h)["connectors"]]
    assert order[0] == "unused"


def test_hook_events_are_counted_but_do_not_inflate_residency(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"srv": {}}})
    d = surface_summary([_turn(), _hook("mcp__srv__t")], home=h)
    c = d["connectors"][0]
    assert c["calls"] == 1
    assert c["turns_resident"] == 1, "a hook event is not a turn"


def test_builtin_tools_are_reported_separately(tmp_path):
    h = _home(tmp_path)
    d = surface_summary([_turn(tools=["Bash", "Bash", "Read"])], home=h)
    assert d["connectors"] == []
    assert d["builtin_tools"][0] == {"tool": "Bash", "calls": 2}


def test_a_connector_seen_only_in_transcripts_is_still_reported(tmp_path):
    """Config may have moved on; a tool that ran is evidence it existed."""
    h = _home(tmp_path)
    c = surface_summary([_turn(tools=["mcp__ghost__t"])], home=h)["connectors"][0]
    assert c["connector"] == "ghost"
    assert c["source"] == "observed in transcripts"


# --- contract ----------------------------------------------------------


def test_schema_columns_are_blank_without_an_inventory(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"srv": {}}})
    d = surface_summary([_turn()], home=h, inventory={})
    assert d["connectors"][0]["schema_tokens"] is None
    assert d["connectors"][0]["carried_tokens"] is None
    assert d["totals"]["have_inventory"] is False


def test_an_inventory_prices_the_connector(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"srv": {}}})
    inv = {"tools": {"mcp__srv__a": 1_000, "mcp__srv__b": 500, "mcp__other__z": 9_999}}
    c = surface_summary([_turn(), _turn()], home=h, inventory=inv)["connectors"][0]
    assert c["schema_tokens"] == 1_500, "only this connector's tools"
    assert c["carried_tokens"] == 1_500 * 2


def test_summary_is_json_serialisable(tmp_path):
    h = _home(tmp_path, root={"mcpServers": {"srv": {}}})
    d = surface_summary([_turn(tools=["mcp__srv__t"])], home=h)
    assert json.loads(json.dumps(d))["totals"]["connectors"] == 1


def test_thresholds_are_published_with_the_result(tmp_path):
    d = surface_summary([], home=_home(tmp_path))
    assert d["thresholds"]["low_use_per_1k_turns"] == LOW_USE_PER_1K_TURNS


def test_empty_input_summarises_to_zero_rather_than_raising(tmp_path):
    d = surface_summary([], home=_home(tmp_path))
    assert d["totals"]["connectors"] == 0
    assert d["totals"]["turns"] == 0
