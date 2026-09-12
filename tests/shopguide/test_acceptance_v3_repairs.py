import json
from pathlib import Path

import pytest
from test_m4 import AgentFixtureGateway, selected, submit

from shopguide.agent.contracts import PlanDecision
from shopguide.intent.service import Taxonomy, apply_delta, empty_state
from shopguide.qa.gateway import GatewayReply
from shopguide.schemas import Cost

pytest_plugins = ["test_m4"]


def test_unused_empty_planner_reason_does_not_break_valid_clarification():
    decision = PlanDecision.model_validate_json(
        json.dumps(
            {"kind": "clarify", "question": "请确认照片上的型号。", "reason": ""}
        )
    )
    assert decision.action().question == "请确认照片上的型号。"
    with pytest.raises(ValueError):
        PlanDecision.model_validate_json(
            json.dumps({"kind": "clarify", "question": "", "reason": ""})
        ).action()


def test_identical_attribute_duplicates_are_audited_but_conflicts_rejected():
    tax = Taxonomy.model_validate_json(
        Path("shopguide/intent/taxonomy.json").read_text()
    )
    state = empty_state("s", tax.version)
    attr = {
        "name": "model",
        "value": "面板",
        "basis": "explicit",
        "evidence": {"quote": "面板", "start": 0, "end": 2},
    }
    delta = {
        "updates": [
            {
                "goal_id": "g1",
                "intent_id": "product.howto",
                "operation": "add",
                "expression": "current",
                "evidence": attr["evidence"],
                "attributes": [attr, attr],
            }
        ]
    }
    updated = apply_delta(
        state,
        delta,
        scope="s",
        taxonomy=tax,
        turn_id="t1",
        message="面板",
        expected_revision=0,
    )
    assert len(updated["goals"]["g1"]["attributes"]) == 1
    assert updated["turns"]["t1"]["normalizations"]
    delta["updates"][0]["attributes"][1] = {**attr, "value": "不同型号"}
    with pytest.raises(ValueError, match="ATTRIBUTE_DUPLICATE"):
        apply_delta(
            state,
            delta,
            scope="s",
            taxonomy=tax,
            turn_id="t1",
            message="面板",
            expected_revision=0,
        )
    assert not state["goals"]


def test_unselected_product_clarifies_without_model_call(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = sessions.create("user_alice", "demo", "snapshot_agent")
    run = submit(sessions, session)

    class NoCalls(AgentFixtureGateway):
        def complete(self, *args):
            raise AssertionError("No model call needed for product selection")

    result = runner(
        NoCalls(), intent_enabled=True, intent_candidates_enabled=False
    ).execute(run["id"], "user_alice")
    assert result["status"] == "WAITING_INPUT" and result["state"]["model_calls"] == 0
    assert result["result"]["missing_fields"] == ["product"]


def test_writer_abstention_is_customer_outcome_not_verification_failure(agent_env):
    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions))

    class Refuse(AgentFixtureGateway):
        def complete(self, role, request, images, schema):
            if role == "write":
                return GatewayReply(
                    {
                        "status": "abstained",
                        "summary": "资料不足",
                        "summary_evidence_ids": [],
                        "steps": [],
                        "prerequisites": [],
                        "unresolved_items": [],
                    },
                    Cost(),
                )
            assert role != "verify"
            return super().complete(role, request, images, schema)

    result = runner(Refuse()).execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    assert result["result"]["status"] == "abstained"
    assert result["result"]["abstention_code"] == "WRITER_ABSTAINED"
    assert "answer" not in result["result"]


@pytest.mark.parametrize("supported", [True, False])
def test_limitations_are_verified_not_silently_discarded(qa, supported):
    from test_m3 import RecordingGateway

    from shopguide.qa.fixed import FixedRAG
    from shopguide.retrieval.models import RRFReranker

    _repo, index, scope, _ev = qa
    text = (
        "当前提供的资料不足以确定故障原因。"
        if supported
        else "拆开外壳进行未授权内部维修。"
    )

    def mutate(role, reply, request):
        if role == "write":
            reply.payload["unresolved_items"] = [text]
        if role == "verify":
            note = next(
                c for c in request["answer"]["claims"] if c["kind"] == "inference"
            )
            assert note["text"] == text
            assert text in request["answer"]["unresolved_items"]
            next(
                c for c in reply.payload["claims"] if c["claim_id"] == note["claim_id"]
            )["supported"] = supported

    result = FixedRAG(index, RRFReranker(), RecordingGateway(mutate)).run(
        "排查故障", scope
    )
    if supported:
        assert (
            result["status"] == "completed" and result["answer"]["status"] == "partial"
        )
        assert result["answer"]["unresolved_items"] == [text]
    else:
        assert result["status"] == "error" and not result["answer"]["steps"]


pytest_plugins = ["test_m3", "test_m4"]


def test_continue_cannot_reactivate_withdrawn_goal():
    tax = Taxonomy.model_validate_json(
        Path("shopguide/intent/taxonomy.json").read_text()
    )
    state = empty_state("s", tax.version)

    def d(op, text):
        return {
            "updates": [
                {
                    "goal_id": "g1",
                    "intent_id": "product.troubleshoot",
                    "operation": op,
                    "expression": "current",
                    "evidence": {"quote": text, "start": 0, "end": len(text)},
                }
            ]
        }

    state = apply_delta(
        state,
        d("add", "无法开机"),
        scope="s",
        taxonomy=tax,
        turn_id="t1",
        message="无法开机",
        expected_revision=0,
    )
    state = apply_delta(
        state,
        d("continue", "还是没反应"),
        scope="s",
        taxonomy=tax,
        turn_id="t2",
        message="还是没反应",
        expected_revision=1,
    )
    assert state["goals"]["g1"]["status"] == "active"
    state = apply_delta(
        state,
        d("withdraw", "不用查了"),
        scope="s",
        taxonomy=tax,
        turn_id="t3",
        message="不用查了",
        expected_revision=2,
    )
    with pytest.raises(ValueError, match="TRANSITION"):
        apply_delta(
            state,
            d("continue", "继续"),
            scope="s",
            taxonomy=tax,
            turn_id="t4",
            message="继续",
            expected_revision=3,
        )


def test_current_query_does_not_repeat_historical_guide(agent_env):
    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions), question="再查一下工单")
    state = run["state"]
    state["intent_ready"] = True
    state["intent_state"] = {
        "goals": {
            "g1": {
                "intent_id": "product.howto",
                "status": "active",
                "expression": "current",
            },
            "g2": {
                "intent_id": "service.progress",
                "status": "active",
                "expression": "current",
            },
        },
        "turns": {
            run["id"]: {
                "delta": {
                    "updates": [
                        {
                            "goal_id": "g2",
                            "intent_id": "service.progress",
                            "operation": "continue",
                            "expression": "current",
                        }
                    ]
                }
            }
        },
    }
    sessions.save(run["id"], "user_alice", state)
    gateway = AgentFixtureGateway(
        [
            {
                "kind": "tool",
                "tool_name": "query_service_requests",
                "arguments": {},
                "reason": "query",
            }
        ]
    )
    result = runner(gateway).execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED" and "service_query" in result["result"]
    assert "answer" not in result["result"] and result["state"]["model_calls"] == 1
    assert result["state"]["intent_state"]["goals"]["g1"]["status"] == "active"
