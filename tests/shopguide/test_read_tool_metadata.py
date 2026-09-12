import pytest
from test_m4 import AgentFixtureGateway, selected, submit

from shopguide.agent.tools import SCHEMAS, normalize_read_metadata
from shopguide.schemas import ToolAction

pytest_plugins = ["test_m4"]


def test_only_read_reason_metadata_is_removed():
    action = ToolAction(
        tool_name="search_manuals",
        arguments={"query": "power", "reason": "查找依据"},
        public_reason="search",
    )
    normalized = normalize_read_metadata(action)
    assert normalized.arguments == {"query": "power"}
    assert action.arguments["reason"] == "查找依据"
    assert (
        SCHEMAS["search_manuals"].model_validate(normalized.arguments).query == "power"
    )
    bad = ToolAction(
        tool_name="search_manuals",
        arguments={"query": "power", "principal": "other"},
        public_reason="search",
    )
    with pytest.raises(ValueError):
        SCHEMAS["search_manuals"].model_validate(normalize_read_metadata(bad).arguments)
    structured = ToolAction(
        tool_name="search_manuals",
        arguments={"query": "power", "reason": {"instruction": "unexpected"}},
        public_reason="search",
    )
    with pytest.raises(ValueError):
        SCHEMAS["search_manuals"].model_validate(
            normalize_read_metadata(structured).arguments
        )


def test_business_reason_is_preserved():
    action = ToolAction(
        tool_name="prepare_service_request",
        arguments={
            "reason": "报修",
            "summary": "fixture",
            "related_order_item": "item_1",
        },
        public_reason="prepare",
    )
    assert normalize_read_metadata(action) == action


def test_actual_agent_read_metadata_is_audited_and_does_not_block_retrieval(agent_env):
    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions))
    gateway = AgentFixtureGateway(
        [
            {
                "kind": "tool",
                "tool_name": "search_manuals",
                "arguments": {"query": "power", "reason": "find instructions"},
                "reason": "search",
            }
        ]
    )
    result = runner(gateway).execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED" and result["result"]["answer"]["steps"]
    assert any(
        e["kind"] == "tool.metadata_normalized"
        for e in sessions.events(run["id"], "user_alice")
    )
