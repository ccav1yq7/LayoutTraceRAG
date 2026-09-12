import json

import pytest
from test_m3 import RecordingGateway
from test_m4 import SimulatedService, prepare_request, submit

from shopguide.agent.diagnostics import failure_detail
from shopguide.intent.service import Delta
from shopguide.qa.fixed import FixedRAG
from shopguide.retrieval.models import RRFReranker

pytest_plugins = ["test_m3", "test_m4"]


def test_waiting_confirmation_intent_survives_but_confirmation_becomes_stale(agent_env):
    _repo, sessions, runner, _root = agent_env
    prepared = prepare_request(sessions, runner)
    state = prepared["state"]
    state["intent_state"] = {
        "scope": "fixture",
        "goals": {"g1": {"intent_id": "service.apply", "status": "active"}},
    }
    sessions.save(prepared["id"], "user_alice", state)
    current = sessions.get(prepared["session"], "user_alice")
    following = submit(sessions, current, "message_pause", "先别申请，我想问怎么启动")
    assert following["state"]["intent_state"] == state["intent_state"]
    assert following["state"]["pending"] is None
    proposal = prepared["result"]
    with pytest.raises(ValueError, match="STALE"):
        SimulatedService(sessions).approve(
            proposal["confirmation_id"],
            "user_alice",
            proposal["arguments_hash"],
            prepared["revision"],
        )


def test_schema_diagnostics_never_log_values_or_unknown_field_names():
    with pytest.raises(ValueError) as caught:
        Delta.model_validate_json(json.dumps({"PRIVATE_SECRET": "PRIVATE_VALUE"}))
    details = failure_detail(caught.value, "intent")
    assert details["reason"] == "SCHEMA_INVALID"
    assert "PRIVATE_" not in json.dumps(details)
    assert details["validation_errors"][0]["loc"] == ["[unknown-field]"]


def test_requested_image_not_used_is_partial_with_notice_and_checked_scope(qa):
    _repo, index, scope, _ev = qa

    def mutate(role, reply, request):
        if role == "select":
            reply.payload["asset_ids"] = []
        if role == "verify":
            assert request["missing_requested_image"] is True

    result = FixedRAG(index, RRFReranker(), RecordingGateway(mutate)).run(
        "展示按钮原图", scope, require_manual_image=True
    )
    assert result["status"] == "completed"
    assert result["answer"]["status"] == "partial"
    assert "原图" in result["answer"]["unresolved_items"][0]
    assert all(not step["display_asset_ids"] for step in result["answer"]["steps"])


def test_optional_image_loss_keeps_warning_without_forcing_incomplete(qa):
    _repo, index, scope, _ev = qa
    result = FixedRAG(index, RRFReranker(), RecordingGateway()).run(
        "power", scope, media_issues=["IMAGE_UNAVAILABLE"]
    )
    assert result["answer"]["unresolved_items"]
    assert "IMAGE_UNAVAILABLE" in result["answer"]["verification"]["warnings"]


def test_created_receipt_closes_only_the_matching_unique_application(agent_env):
    from test_m4 import AgentFixtureGateway

    _repo, sessions, runner, _root = agent_env
    prepared = prepare_request(sessions, runner)
    state = prepared["state"]
    state["intent_ready"] = True
    state["intent_state"] = {
        "goals": {
            "g1": {
                "intent_id": "service.apply",
                "status": "active",
                "expression": "current",
            }
        }
    }
    sessions.save(prepared["id"], "user_alice", state)
    proposal = prepared["result"]
    SimulatedService(sessions).approve(
        proposal["confirmation_id"],
        "user_alice",
        proposal["arguments_hash"],
        prepared["revision"],
    )
    completed = runner().execute(prepared["id"], "user_alice")
    assert completed["status"] == "COMPLETED"
    ticket = completed["result"]["service_request"]["ticket_id"]
    follow = submit(
        sessions,
        sessions.get(prepared["session"], "user_alice"),
        "message_query_receipt",
        "查询刚才创建的工单",
    )
    assert follow["state"]["fulfilled_business_goals"] == {"g1": ticket}
    gateway = AgentFixtureGateway(
        [
            {
                "kind": "tool",
                "tool_name": "query_service_requests",
                "arguments": {"ticket_id": ticket},
                "reason": "query actual receipt",
            }
        ]
    )
    result = runner(gateway).execute(follow["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    assert result["result"]["service_query"]["tickets"][0]["ticket_id"] == ticket


def test_intent_validation_repair_is_bounded_and_does_not_commit_bad_delta():
    from pathlib import Path

    from shopguide.intent.service import Taxonomy, empty_state, understand
    from shopguide.qa.gateway import GatewayReply
    from shopguide.schemas import Cost

    taxonomy = Taxonomy.model_validate_json(
        Path("shopguide/intent/taxonomy.json").read_text()
    )
    state = empty_state("s", taxonomy.version)

    class Gateway:
        calls = 0

        def complete(self, role, request, images, schema):
            self.calls += 1
            quote = "伪造原文" if self.calls == 1 else "查询工单"
            if self.calls == 2:
                assert (
                    request["validation_repair"]["reason"] == "INTENT_EVIDENCE_INVALID"
                )
            return GatewayReply(
                {
                    "updates": [
                        {
                            "goal_id": "g1",
                            "intent_id": "service.progress",
                            "operation": "add",
                            "expression": "current",
                            "evidence": {"quote": quote, "start": 0, "end": 4},
                        }
                    ]
                },
                Cost(),
            )

    gateway = Gateway()
    result = understand(
        gateway,
        taxonomy,
        state,
        scope="s",
        turn_id="t1",
        message="查询工单",
        history=[],
        repair_once=True,
    )
    assert gateway.calls == 2 and result["revision"] == 1 and state["revision"] == 0


def test_transport_failures_are_not_retried_by_intent_repair():
    from pathlib import Path

    from shopguide.intent.service import Taxonomy, empty_state, understand

    taxonomy = Taxonomy.model_validate_json(
        Path("shopguide/intent/taxonomy.json").read_text()
    )

    class Gateway:
        calls = 0

        def complete(self, *args):
            self.calls += 1
            raise RuntimeError("MODEL_TIMEOUT")

    gateway = Gateway()
    with pytest.raises(RuntimeError, match="MODEL_TIMEOUT"):
        understand(
            gateway,
            taxonomy,
            empty_state("s", taxonomy.version),
            scope="s",
            turn_id="t1",
            message="hi",
            history=[],
            repair_once=True,
        )
    assert gateway.calls == 1
