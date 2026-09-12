from pathlib import Path

import pytest
from test_m4 import AgentFixtureGateway, selected, submit

from shopguide.agent.customer_messages import (
    control_acknowledgement,
    insufficient_information_message,
)
from shopguide.intent.service import Taxonomy, apply_delta, empty_state
from shopguide.qa.gateway import GatewayReply
from shopguide.schemas import Cost

pytest_plugins = ["test_m4"]
TAX = Taxonomy.model_validate_json(
    Path("shopguide/intent/taxonomy.json").read_text()
)


def delta(op, message):
    return {
        "updates": [
            {
                "goal_id": "g1",
                "intent_id": "service.apply",
                "operation": op,
                "expression": "current",
                "evidence": {"quote": message, "start": 0, "end": len(message)},
            }
        ]
    }


def test_repeat_withdraw_is_safe_and_never_resurrects():
    state = empty_state("s", TAX.version)
    for t, op, msg in [
        ("t1", "add", "申请维修"),
        ("t2", "withdraw", "不申请了"),
        ("t3", "withdraw", "不申请了"),
    ]:
        state = apply_delta(
            state,
            delta(op, msg),
            scope="s",
            taxonomy=TAX,
            turn_id=t,
            message=msg,
            expected_revision=state["revision"],
        )
    assert state["goals"]["g1"]["status"] == "withdrawn"
    assert "已撤回" in control_acknowledgement(state, "t3", {})
    assert "工单仍保留" in control_acknowledgement(state, "t3", {"g1": "ticket_one"})
    with pytest.raises(ValueError, match="TRANSITION"):
        apply_delta(
            state,
            delta("resume", "继续申请"),
            scope="s",
            taxonomy=TAX,
            turn_id="t4",
            message="继续申请",
            expected_revision=state["revision"],
        )


def test_control_turn_does_not_reanswer_old_howto_goal(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session, question="先别申请")
    state = run["state"]
    scope = "user_alice:" + state["session_id"] + ":" + state["task_id"]
    previous = empty_state(scope, TAX.version)
    previous = apply_delta(
        previous,
        delta("add", "申请维修"),
        scope=scope,
        taxonomy=TAX,
        turn_id="old_turn",
        message="申请维修",
        expected_revision=0,
    )
    previous["goals"]["old_guide"] = {
        "intent_id": "product.howto",
        "status": "active",
        "expression": "current",
        "attributes": {},
        "history": [],
    }
    state["intent_state"] = previous
    sessions.save(run["id"], "user_alice", state)

    class Gateway(AgentFixtureGateway):
        def complete(self, role, request, images, schema):
            assert role == "intent", (
                "A pure stop request must not invoke planner or writer"
            )
            return GatewayReply(delta("suspend", request["current_message"]), Cost())

    result = runner(
        Gateway(), intent_enabled=True, intent_candidates_enabled=False
    ).execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    assert result["result"]["status"] == "acknowledged"
    assert "暂停" in result["result"]["message"]
    assert result["state"]["model_calls"] == 1 and result["state"]["planner_steps"] == 0
    assert result["state"]["intent_state"]["goals"]["old_guide"]["status"] == "active"


def test_policy_refusal_does_not_ask_for_already_selected_model():
    state = {
        "product_id": "product_a",
        "intent_state": {
            "goals": {
                "g1": {
                    "intent_id": "service.rules",
                    "status": "active",
                    "expression": "current",
                }
            }
        },
    }
    message = insufficient_information_message(state)
    assert "售后规则" in message and "型号" not in message


def test_pause_with_new_question_is_not_short_circuited():
    state = empty_state("s", TAX.version)
    state = apply_delta(
        state,
        delta("add", "申请维修"),
        scope="s",
        taxonomy=TAX,
        turn_id="t1",
        message="申请维修",
        expected_revision=0,
    )
    message = "先别申请，怎么启动？"
    changes = delta("suspend", message)
    changes["updates"].append(
        {
            "goal_id": "g2",
            "intent_id": "product.howto",
            "operation": "add",
            "expression": "current",
            "evidence": {"quote": message, "start": 0, "end": len(message)},
        }
    )
    state = apply_delta(
        state,
        changes,
        scope="s",
        taxonomy=TAX,
        turn_id="t2",
        message=message,
        expected_revision=state["revision"],
    )
    assert control_acknowledgement(state, "t2", {}) is None
    assert state["goals"]["g2"]["status"] == "active"
