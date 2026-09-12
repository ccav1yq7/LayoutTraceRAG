import json
import time

import pytest
from sqlalchemy import text
from test_m2 import add_page

from shopguide.agent.contracts import AgentBudget
from shopguide.agent.fixtures import AgentFixtureGateway
from shopguide.agent.ledger import SimulatedService, ToolLedger
from shopguide.agent.runtime import AgentRunner
from shopguide.retrieval.models import HashEmbedder, RRFReranker
from shopguide.retrieval.store import ScopedIndex
from shopguide.schemas import OrderItem
from shopguide.sessions.store import Sessions
from shopguide.storage.repository import Repository
from shopguide.storage.snapshots import Snapshots


@pytest.fixture
def agent_env(tmp_path):
    repo = Repository(tmp_path / "metadata.db")
    snap = Snapshots(repo)
    snap.begin("snapshot_agent", "demo", {})
    evidence = add_page(
        repo,
        tmp_path,
        "snapshot_agent",
        1,
        "Press the power button once. Do not open the housing.",
    )
    evidence += add_page(
        repo,
        tmp_path,
        "snapshot_agent",
        2,
        "Hold the wireless switch for the second device.",
    )
    evidence += add_page(
        repo,
        tmp_path,
        "snapshot_agent",
        3,
        "Private device of another user.",
        "user_bob",
    )
    for i, user in [(1, "user_alice"), (2, "user_alice"), (3, "user_bob")]:
        item = OrderItem(
            order_item_id=f"item_{i}",
            principal_id=user,
            product_id=f"product_{i}",
            variant_id=f"variant_{i}",
            order_id=f"order_{user}",
        )
        repo.put(item, item.order_item_id)
    index = ScopedIndex(
        repo,
        tmp_path / "assets",
        tmp_path / "indexes",
        "snapshot_agent",
        HashEmbedder(),
    )
    index.build(evidence)
    index.publish(expected_active=None)
    sessions = Sessions(repo)

    def factory(snapshot):
        return ScopedIndex(
            repo, tmp_path / "assets", tmp_path / "indexes", snapshot, HashEmbedder()
        )

    def runner(gateway=None, **kwargs):
        return AgentRunner(
            repo,
            tmp_path,
            factory,
            RRFReranker(),
            gateway or AgentFixtureGateway(),
            **kwargs,
        )

    yield repo, sessions, runner, tmp_path
    repo.close()


def selected(sessions, product=1):
    session = sessions.create("user_alice", "demo", "snapshot_agent")
    return sessions.select(
        session["id"],
        "user_alice",
        f"product_{product}",
        f"variant_{product}",
        session["revision"],
    )


def submit(sessions, session, message="message_one", question="How do I start it?"):
    return sessions.submit(
        session["id"], "user_alice", message, question, session["revision"]
    )


def test_agent_search_inspect_finalize_and_multiturn(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    result = runner().execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED", result
    assert result["result"]["answer"]["steps"]
    assert result["state"]["model_calls"] == 6
    assert result["state"]["image_inputs"] == 3
    assert result["result"]["answer"]["verification"]["semantic"] == "not_checked"
    kinds = [e["kind"] for e in sessions.events(run["id"], "user_alice")]
    assert "planner.decided" in kinds and "tool.completed" in kinds
    session = sessions.get(session["id"], "user_alice")
    follow = submit(sessions, session, "message_two", "Where is that button?")
    assert follow["state"]["history"] and follow["state"]["evidence_ids"]
    resumed = runner().execute(follow["id"], "user_alice")
    assert resumed["status"] == "COMPLETED"
    assert resumed["state"]["model_calls"] == 3


def test_clarification_then_user_selection(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = sessions.create("user_alice", "demo", "snapshot_agent")
    run = submit(sessions, session)
    result = runner().execute(run["id"], "user_alice")
    assert result["status"] == "WAITING_INPUT"
    current = sessions.get(session["id"], "user_alice")
    current = sessions.select(
        current["id"], "user_alice", "product_1", "variant_1", current["revision"]
    )
    next_run = submit(sessions, current, "message_two", "Use this product.")
    assert next_run["state"]["history"]
    assert runner().execute(next_run["id"], "user_alice")["status"] == "COMPLETED"


def test_message_replay_conflict_and_busy(agent_env):
    _repo, sessions, _runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    assert submit(sessions, session)["id"] == run["id"]
    with pytest.raises(ValueError, match="IDEMPOTENCY"):
        submit(sessions, session, question="different")
    with pytest.raises((ValueError, RuntimeError)):
        submit(sessions, session, "message_two")
    with pytest.raises(PermissionError):
        sessions.run(run["id"], "user_bob")
    with pytest.raises(PermissionError):
        sessions.select(session["id"], "user_bob", "product_3", "variant_3", 1)


def test_product_switch_resets_working_evidence(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    runner().execute(run["id"], "user_alice")
    current = sessions.get(session["id"], "user_alice")
    old_task = current["task"]
    current = sessions.select(
        current["id"], "user_alice", "product_2", "variant_2", current["revision"]
    )
    switched = submit(
        sessions, current, "message_two", "How do I use the wireless switch?"
    )
    assert current["task"] != old_task
    assert not switched["state"]["evidence_ids"] and not switched["state"]["history"]
    result = runner().execute(switched["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    assert result["result"]["answer"]["product_ref"] == "product_2"


def test_budget_reserves_finalization(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    result = runner(budget=AgentBudget(max_model_calls=3)).execute(
        run["id"], "user_alice"
    )
    assert result["status"] == "COMPLETED"
    assert result["state"]["model_calls"] == 3
    # Finalization has grounded text; optional images do not decide completeness.
    assert result["result"]["answer"]["status"] == "answered"


def test_no_progress_is_bounded(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    decisions = [
        {
            "kind": "tool",
            "tool_name": "list_purchased_items",
            "arguments_json": "{}",
            "reason": "List candidates",
        }
    ] * 8
    result = runner(AgentFixtureGateway(decisions)).execute(run["id"], "user_alice")
    assert result["status"] == "FAILED" and result["result"]["code"] == "NO_PROGRESS"
    assert result["state"]["tool_attempts"] == 3


class Crash(BaseException):
    pass


def test_restart_after_tool_checkpoint_does_not_repeat_read(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)

    def crash(stage, state):
        if stage == "tool":
            raise Crash()

    with pytest.raises(Crash):
        runner(checkpoint_hook=crash).execute(run["id"], "user_alice")
    saved = sessions.run(run["id"], "user_alice")
    assert saved["state"]["evidence_ids"] and saved["state"]["pending"] is None
    result = runner().execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    search_events = [
        e
        for e in sessions.events(run["id"], "user_alice")
        if e["kind"] == "tool.completed" and e["payload"]["name"] == "search_manuals"
    ]
    assert len(search_events) == 1


def test_cancel_and_rate_limit(agent_env):
    _repo, sessions, runner, _root = agent_env
    one = selected(sessions)
    two = selected(sessions)
    three = selected(sessions)
    run1 = submit(sessions, one)
    run2 = submit(sessions, two)
    with pytest.raises(RuntimeError, match="RATE_LIMITED"):
        submit(sessions, three)
    sessions.cancel(run1["id"], "user_alice")
    assert runner().execute(run1["id"], "user_alice")["status"] == "CANCELLED"
    assert submit(sessions, three)["status"] == "QUEUED"
    sessions.cancel(run2["id"], "user_alice")


def prepare_request(sessions, runner):
    session = selected(sessions)
    run = submit(
        sessions, session, question="Please prepare a simulated service ticket."
    )
    args = {
        "reason": "fixture repair",
        "related_order_item": "item_1",
        "summary": "Synthetic service request",
    }
    decision = {
        "kind": "tool",
        "tool_name": "prepare_service_request",
        "arguments_json": json.dumps(args),
        "reason": "Prepare for user confirmation",
    }
    result = runner(AgentFixtureGateway([decision])).execute(run["id"], "user_alice")
    assert result["status"] == "WAITING_CONFIRMATION", result
    return result


def test_human_confirmation_and_exact_local_ticket(agent_env):
    repo, sessions, runner, _root = agent_env
    prepared = prepare_request(sessions, runner)
    proposal = prepared["result"]
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM sg_tickets")).scalar_one() == 0
    service = SimulatedService(sessions)
    service.approve(
        proposal["confirmation_id"],
        "user_alice",
        proposal["arguments_hash"],
        prepared["revision"],
    )
    result = runner().execute(prepared["id"], "user_alice")
    assert (
        result["status"] == "COMPLETED"
        and result["result"]["service_request"]["simulated"]
    )
    replay = runner().execute(prepared["id"], "user_alice")
    assert replay["result"] == result["result"]
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM sg_tickets")).scalar_one() == 1


@pytest.mark.parametrize("change", ["owner", "hash", "revision", "expiry", "product"])
def test_stale_confirmation_rejected(agent_env, change):
    repo, sessions, runner, _root = agent_env
    prepared = prepare_request(sessions, runner)
    proposal = prepared["result"]
    service = SimulatedService(sessions)
    principal = "user_bob" if change == "owner" else "user_alice"
    digest = "bad" if change == "hash" else proposal["arguments_hash"]
    revision = prepared["revision"] + (change == "revision")
    if change == "expiry":
        with sessions.transaction() as c:
            c.execute(
                text("UPDATE sg_confirmations SET expires=:e"), {"e": time.time() - 1}
            )
    if change == "product":
        current = sessions.get(prepared["session"], "user_alice")
        sessions.select(
            current["id"], "user_alice", "product_2", "variant_2", current["revision"]
        )
    with pytest.raises((PermissionError, ValueError)):
        service.approve(proposal["confirmation_id"], principal, digest, revision)
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM sg_tickets")).scalar_one() == 0


def test_unknown_effect_not_reexecuted(agent_env):
    _repo, sessions, _runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    ledger = ToolLedger(sessions)
    effects = []

    def effect():
        effects.append("done")
        raise Crash()

    with pytest.raises(Crash):
        ledger.execute(run["id"], "external_mock", {}, effect, side_effect=True)
    result, executed, _call = ledger.execute(
        run["id"], "external_mock", {}, effect, side_effect=True
    )
    assert (
        result["code"] == "OUTCOME_UNKNOWN"
        and executed is False
        and effects == ["done"]
    )


def test_ticket_response_loss_reconciles_after_expiry(agent_env, monkeypatch):
    repo, sessions, runner, _root = agent_env
    prepared = prepare_request(sessions, runner)
    proposal = prepared["result"]
    SimulatedService(sessions).approve(
        proposal["confirmation_id"],
        "user_alice",
        proposal["arguments_hash"],
        prepared["revision"],
    )
    original = ToolLedger._save

    def crash_after_effect(self, call_id, state, result):
        if result.get("ticket_id"):
            raise Crash()
        return original(self, call_id, state, result)

    monkeypatch.setattr(ToolLedger, "_save", crash_after_effect)
    with pytest.raises(Crash):
        runner().execute(prepared["id"], "user_alice")
    monkeypatch.setattr(ToolLedger, "_save", original)
    saved = sessions.run(prepared["id"], "user_alice")
    saved["state"]["deadline"] = time.time() - 1
    sessions.save(prepared["id"], "user_alice", saved["state"])
    with sessions.transaction() as c:
        c.execute(
            text("UPDATE sg_confirmations SET expires=:e"), {"e": time.time() - 1}
        )
    result = runner().execute(prepared["id"], "user_alice")
    assert result["status"] == "COMPLETED" and result["result"]["reconciled"] is True
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM sg_tickets")).scalar_one() == 1


def test_verification_failure_returns_to_planner(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)

    class RepairGateway(AgentFixtureGateway):
        def complete(self, role, request, images, schema):
            if role == "plan" and request["repair"]:
                from shopguide.qa.gateway import GatewayReply
                from shopguide.schemas import Cost

                return GatewayReply(
                    {"kind": "abstain", "reason": "Evidence support is insufficient"},
                    Cost(),
                )
            reply = super().complete(role, request, images, schema)
            if role == "verify":
                reply.payload["claims"][0]["supported"] = False
            return reply

    result = runner(RepairGateway()).execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED" and result["result"]["status"] == "abstained"
    assert result["state"]["repair"] and result["state"]["candidate_draft"]
    assert any(
        e["kind"] == "verification.failed"
        for e in sessions.events(run["id"], "user_alice")
    )


def test_cancel_during_model_request(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)

    class CancelGateway(AgentFixtureGateway):
        def complete(self, *args):
            sessions.cancel(run["id"], "user_alice")
            return super().complete(*args)

    result = runner(CancelGateway()).execute(run["id"], "user_alice")
    assert result["status"] == "CANCELLED" and result["state"]["model_calls"] == 1
    assert not result["state"]["evidence_ids"]


def test_shared_tool_comparison_keeps_budgets_and_costs(agent_env):
    repo, _sessions, _runner, root = agent_env
    from shopguide.agent.comparison import compare

    def factory(snapshot):
        return ScopedIndex(
            repo, root / "assets", root / "indexes", snapshot, HashEmbedder()
        )

    report = compare(
        repo,
        root,
        factory,
        RRFReranker(),
        AgentFixtureGateway,
        "user_alice",
        "product_1",
        "variant_1",
        "snapshot_agent",
        "How do I start it?",
    )
    fixed = report["results"]["B2_shared_tools"]
    agent = report["results"]["B3"]
    assert fixed["status"] == agent["status"] == "COMPLETED"
    assert (
        fixed["configuration"]["budget"]
        == agent["configuration"]["budget"]
        == report["budget"]
    )
    assert fixed["result"]["usage"]["model_calls"] == 3
    assert agent["result"]["usage"]["model_calls"] == 6
    assert report["official_benchmark"] is False


def test_resume_rejects_changed_model_configuration(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)

    def crash(stage, state):
        if stage == "tool":
            raise Crash()

    with pytest.raises(Crash):
        runner(checkpoint_hook=crash).execute(run["id"], "user_alice")
    changed = AgentFixtureGateway()
    changed.model_id = "fixture/changed"
    with pytest.raises(RuntimeError, match="RUN_CONFIG_CHANGED"):
        runner(changed).execute(run["id"], "user_alice")


def test_cancel_after_effect_reports_persisted_ticket(agent_env, monkeypatch):
    repo, sessions, runner, _root = agent_env
    prepared = prepare_request(sessions, runner)
    proposal = prepared["result"]
    SimulatedService(sessions).approve(
        proposal["confirmation_id"],
        "user_alice",
        proposal["arguments_hash"],
        prepared["revision"],
    )
    original = SimulatedService.commit

    def cancel_after_commit(self, confirmation, principal, run_id):
        result = original(self, confirmation, principal, run_id)
        sessions.cancel(run_id, principal)
        return result

    monkeypatch.setattr(SimulatedService, "commit", cancel_after_commit)
    result = runner().execute(prepared["id"], "user_alice")
    assert result["status"] == "CANCELLED"
    assert result["result"]["service_request"]["ticket_id"]
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM sg_tickets")).scalar_one() == 1


def test_normalized_tool_arguments_share_no_progress_key(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    decisions = [
        {
            "kind": "tool",
            "tool_name": "list_purchased_items",
            "arguments_json": args,
            "reason": "List candidates",
        }
        for args in ("{}", '{"query":""}', '{ "query" : "" }')
    ]
    result = runner(AgentFixtureGateway(decisions)).execute(run["id"], "user_alice")
    assert result["result"]["code"] == "NO_PROGRESS"
    events = [
        e
        for e in sessions.events(run["id"], "user_alice")
        if e["kind"] == "tool.completed"
    ]
    assert [e["payload"]["executed"] for e in events] == [True, False, False]


def test_one_invalid_plan_is_repaired_and_charged(agent_env):
    _repo, sessions, runner, _root = agent_env
    session = selected(sessions)
    run = submit(sessions, session)
    result = runner(
        AgentFixtureGateway([{"kind": "not_an_action", "reason": "invalid fixture"}])
    ).execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    assert (
        result["state"]["parse_failures"] == 1 and result["state"]["model_calls"] == 7
    )


def test_typed_planner_arguments_reject_scope_fields():
    from pydantic import ValidationError

    from shopguide.agent.contracts import PlanDecision

    payload = {
        "kind": "tool",
        "tool_name": "search_manuals",
        "arguments": {"query": "power", "top_k": 3},
        "reason": "Read source",
    }
    assert PlanDecision.model_validate(payload).action().arguments == {
        "query": "power",
        "top_k": 3,
    }
    payload["arguments"]["principal_id"] = "user_bob"
    with pytest.raises(ValidationError):
        PlanDecision.model_validate(payload)


def test_revoked_working_evidence_is_not_sent_back_to_planner(agent_env):
    repo, sessions, runner, _root = agent_env
    from shopguide.schemas import Evidence

    session = selected(sessions)
    run = submit(sessions, session)

    def crash(stage, state):
        if stage == "tool":
            raise Crash()

    with pytest.raises(Crash):
        runner(checkpoint_hook=crash).execute(run["id"], "user_alice")
    record = sessions.run(run["id"], "user_alice")
    for eid in record["state"]["evidence_ids"]:
        repo.revoke(repo.get(Evidence, eid).source_locator.doc_version_id)
    result = runner().execute(run["id"], "user_alice")
    assert (
        result["status"] == "FAILED"
        and result["result"]["code"] == "SOURCE_SCOPE_UNAVAILABLE"
    )
    assert result["state"]["model_calls"] == 1
