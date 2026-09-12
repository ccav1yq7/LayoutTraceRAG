import copy
from pathlib import Path

import pytest

from shopguide.intent.service import Taxonomy, apply_delta, empty_state

TAX = Taxonomy.model_validate_json(
    Path("shopguide/intent/taxonomy.json").read_text()
)


def change(message, op="add", goal="g1", intent="service.apply", expression="current"):
    return {
        "updates": [
            {
                "goal_id": goal,
                "intent_id": intent,
                "operation": op,
                "expression": expression,
                "evidence": {"quote": message, "start": 0, "end": len(message)},
            }
        ]
    }


def apply(state, msg, delta, turn="t1", revision=None, scope="scope"):
    return apply_delta(
        state,
        delta,
        scope=scope,
        taxonomy=TAX,
        turn_id=turn,
        message=msg,
        expected_revision=state["revision"] if revision is None else revision,
    )


def test_suspend_query_resume_withdraw_and_no_resurrection():
    state = empty_state("scope", TAX.version)
    state = apply(state, "申请维修", change("申请维修"))
    original = copy.deepcopy(state)
    state = apply(state, "先别申请", change("先别申请", "suspend"), "t2")
    assert original["goals"]["g1"]["status"] == "active"
    state = apply(
        state, "怎么收费", change("怎么收费", goal="g2", intent="service.rules"), "t3"
    )
    assert state["goals"]["g1"]["status"] == "suspended"
    state = apply(state, "继续申请", change("继续申请", "resume"), "t4")
    state = apply(state, "不申请了", change("不申请了", "withdraw"), "t5")
    with pytest.raises(ValueError, match="TRANSITION"):
        apply(state, "继续", change("继续", "resume"), "t6")
    assert state["goals"]["g2"]["status"] == "active"


def test_replay_scope_version_and_evidence():
    base = empty_state("scope", TAX.version)
    state = apply(base, "申请", change("申请"))
    assert apply(state, "申请", change("申请"), revision=0) == state
    for kwargs in [{"scope": "other"}, {"revision": 0}]:
        with pytest.raises(ValueError):
            apply(state, "新消息", change("新消息", goal="g2"), "t2", **kwargs)
    with pytest.raises(ValueError, match="TURN_CONFLICT"):
        apply(state, "不同内容", change("不同内容"))
    with pytest.raises(ValueError, match="EVIDENCE"):
        apply(base, "申请", change("伪造引用"))
    assert base["goals"] == {}


def test_ambiguity_is_not_multiple_goals_and_conditional_not_active():
    base = empty_state("scope", TAX.version)
    state = apply(
        base, "如果不行再维修", change("如果不行再维修", expression="conditional")
    )
    assert state["goals"]["g1"]["status"] == "pending"
    state = apply(
        state,
        "处理一下",
        {
            "ambiguities": [
                {
                    "kind": "intent",
                    "candidates": ["service.apply", "service.rules"],
                    "question": "您想咨询方法还是申请办理？",
                    "evidence": {"quote": "处理一下", "start": 0, "end": 4},
                }
            ]
        },
        "t2",
    )
    assert len(state["goals"]) == 1 and len(state["ambiguities"]) == 1


def test_atomic_rejection_unknown_label_and_negated_add():
    base = empty_state("scope", TAX.version)
    for delta in [
        change("退款", intent="invented"),
        change("退款", expression="negated"),
    ]:
        with pytest.raises(ValueError):
            apply(base, "退款", delta)
    assert base["revision"] == 0


def test_agent_understanding_failure_stops_before_planning(agent_env):
    from test_m4 import selected, submit

    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    agent = runner()
    agent.intent_enabled = True  # Existing scripted fixture has no intent capability.
    result = agent.execute(run["id"], "user_alice")
    assert result["status"] == "FAILED"
    assert result["result"]["code"] == "INTENT_UNDERSTANDING_FAILED"
    assert result["state"]["planner_steps"] == 0


pytest_plugins = ["test_m4"]


def test_intent_is_metered_persisted_and_inherited(agent_env):
    from test_m4 import AgentFixtureGateway, selected, submit

    from shopguide.qa.gateway import GatewayReply
    from shopguide.schemas import Cost

    class IntentFixture(AgentFixtureGateway):
        def complete(self, role, request, images, schema):
            if role == "intent":
                return GatewayReply(
                    change(request["current_message"], intent="product.howto"), Cost()
                )
            return super().complete(role, request, images, schema)

    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    agent = runner()
    agent.intent_enabled = True
    agent.gateway = IntentFixture()
    result = agent.execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    assert result["state"]["intent_state"]["revision"] == 1
    roles = [
        e["payload"]["role"]
        for e in sessions.events(run["id"], "user_alice")
        if e["kind"] == "model.started"
    ]
    assert roles[0] == "intent" and roles.count("intent") == 1
    current = sessions.get(session["id"], "user_alice")
    following = submit(sessions, current, "message_followup", "还是不行")
    assert following["state"]["intent_state"] == result["state"]["intent_state"]
    assert not following["state"]["intent_ready"]


def test_unique_quote_alignment_is_audited_but_ambiguous_quote_rejected():
    base = empty_state("scope", TAX.version)
    delta = change("维修")
    delta["updates"][0]["evidence"]["start"] = 1
    delta["updates"][0]["evidence"]["end"] = 3
    state = apply(base, "申请维修", delta)
    assert state["goals"]["g1"]["history"][0]["evidence"]["start"] == 2
    assert state["turns"]["t1"]["evidence_alignments"]
    with pytest.raises(ValueError, match="EVIDENCE"):
        apply(base, "维修和维修", delta)


@pytest.mark.parametrize(
    "status,ready,uncertain",
    [
        ("suspended", True, False),
        ("withdrawn", True, False),
        ("active", False, False),
        ("active", True, True),
    ],
)
def test_service_preparation_blocked_without_current_understanding(
    status, ready, uncertain
):
    from types import SimpleNamespace

    from shopguide.agent.tools import AgentTools
    from shopguide.schemas import ToolAction

    tools = object.__new__(AgentTools)
    tools.principal = "user_alice"
    tools.repo = SimpleNamespace(
        orders=lambda principal: [SimpleNamespace(order_item_id="item_1")]
    )
    tools.scope = lambda: None
    tools.state = {
        "intent_ready": ready,
        "intent_uncertain": uncertain,
        "intent_state": {
            "ambiguities": [],
            "goals": {
                "g1": {
                    "intent_id": "service.apply",
                    "status": status,
                    "expression": "current",
                }
            },
        },
    }
    with pytest.raises(ValueError, match="INTENT_DOES_NOT_ALLOW"):
        tools.execute(
            ToolAction(
                tool_name="prepare_service_request",
                arguments={
                    "summary": "repair",
                    "reason": "fixture",
                    "related_order_item": "item_1",
                },
                public_reason="fixture",
            )
        )
