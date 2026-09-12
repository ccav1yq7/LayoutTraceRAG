import pytest
from sqlalchemy import text
from test_m4 import AgentBudget, AgentFixtureGateway, selected, submit

from shopguide.qa.gateway import GatewayReply
from shopguide.schemas import Cost

pytest_plugins = ["test_m4"]


class WorkerFixture(AgentFixtureGateway):
    def __init__(self, worker="guide", malicious=None):
        super().__init__(
            [
                {
                    "kind": "delegate",
                    "specialist": worker,
                    "task": "Find the power instructions",
                    "reason": "specialized retrieval",
                }
            ]
        )
        self.malicious = malicious

    def complete(self, role, request, images, schema):
        if role.startswith("specialist_"):
            assert "commit_service_request" not in request["tools"]
            if self.malicious:
                return GatewayReply(
                    {
                        "kind": "tool",
                        "tool_name": self.malicious,
                        "arguments": {},
                        "reason": "fixture attack",
                    },
                    Cost(),
                )
            if not request["evidence"]:
                return GatewayReply(
                    {
                        "kind": "tool",
                        "tool_name": "search_manuals",
                        "arguments": {"query": "power", "top_k": 8},
                        "reason": "find evidence",
                    },
                    Cost(),
                )
            return GatewayReply(
                {
                    "kind": "done",
                    "evidence_ids": [e["evidence_id"] for e in request["evidence"]],
                    "reason": "evidence found",
                },
                Cost(),
            )
        return super().complete(role, request, images, schema)


def test_supervisor_delegates_and_still_verifies_final_answer(agent_env):
    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions))
    agent = runner(WorkerFixture())
    agent.delegation_enabled = True
    result = agent.execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    state = result["state"]
    assert state["delegation"] is None
    report = state["specialist_reports"][0]
    assert report["specialist"] == "guide" and report["outcome"] == "done"
    assert report["evidence_ids"] and report["calls"] == 2
    roles = [
        e["payload"]["role"]
        for e in sessions.events(run["id"], "user_alice")
        if e["kind"] == "model.started"
    ]
    assert roles.count("specialist_guide") == 2 and roles[-2:] == ["write", "verify"]
    assert state["model_calls"] == len(roles) <= 12


@pytest.mark.parametrize(
    "worker,tool",
    [("guide", "commit_service_request"), ("policy", "inspect_user_image")],
)
def test_worker_cannot_escape_tool_allowlist(agent_env, worker, tool):
    repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions))
    agent = runner(WorkerFixture(worker, tool))
    agent.delegation_enabled = True
    result = agent.execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    assert result["state"]["specialist_reports"][0]["outcome"] == "failed"
    with repo.engine.connect() as c:
        assert (
            c.execute(text("SELECT COUNT(*) FROM sg_confirmations")).scalar_one() == 0
        )
        assert c.execute(text("SELECT COUNT(*) FROM sg_tickets")).scalar_one() == 0


def test_small_budget_preserves_supervisor_and_finalization(agent_env):
    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions))
    agent = runner(WorkerFixture(), budget=AgentBudget(max_model_calls=4))
    agent.delegation_enabled = True
    result = agent.execute(run["id"], "user_alice")
    assert result["state"]["specialist_reports"][0]["outcome"] == "budget_exhausted"
    assert result["state"]["specialist_reports"][0]["calls"] == 0
    assert result["state"]["model_calls"] <= 4


def test_delegated_read_resumes_without_replanning_worker_action(agent_env):
    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions))
    gateway = WorkerFixture()
    agent = runner(gateway)
    agent.delegation_enabled = True

    def interrupt(stage, state):
        if stage == "specialist_decided":
            raise SystemExit("fixture restart")

    agent.checkpoint_hook = interrupt
    with pytest.raises(SystemExit):
        agent.execute(run["id"], "user_alice")
    saved = sessions.run(run["id"], "user_alice")["state"]
    assert saved["delegation"]["pending_tool"]["tool_name"] == "search_manuals"
    resumed = runner(gateway)
    resumed.delegation_enabled = True
    result = resumed.execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    events = sessions.events(run["id"], "user_alice")
    assert (
        sum(
            e["kind"] == "tool.completed" and e["payload"]["name"] == "search_manuals"
            for e in events
        )
        == 1
    )
    assert result["state"]["specialist_reports"][0]["calls"] == 2


def seed_goals(sessions, run, status="active"):
    state = run["state"]
    state["intent_ready"] = True
    state["intent_state"] = {
        "goals": {
            "g_query": {
                "intent_id": "service.progress",
                "status": "active",
                "expression": "current",
            },
            "g_guide": {
                "intent_id": "product.howto",
                "status": status,
                "expression": "current",
            },
        }
    }
    sessions.save(run["id"], "user_alice", state)


def test_query_does_not_drop_other_goal_and_final_response_keeps_both(agent_env):
    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions), question="查工单，并说明怎么开机")
    seed_goals(sessions, run)
    seeded = sessions.run(run["id"], "user_alice")["state"]
    seeded["intent_state"]["goals"]["g_guide"]["history"] = [
        {"source_turn_id": run["id"], "evidence": {"quote": "说明怎么开机"}}
    ]
    sessions.save(run["id"], "user_alice", seeded)

    class ContextFixture(WorkerFixture):
        def complete(self, role, request, images, schema):
            if role in ("write", "verify"):
                assert (
                    request["separately_delivered_service_query"]["status"] == "empty"
                )
                assert request["separately_delivered_service_query"]["ticket_ids"] == []
            return super().complete(role, request, images, schema)

    gateway = ContextFixture()
    gateway.decisions[0]["goal_id"] = "g_guide"
    gateway.decisions.appendleft(
        {
            "kind": "tool",
            "tool_name": "query_service_requests",
            "arguments": {},
            "reason": "lookup",
        }
    )
    agent = runner(gateway)
    agent.delegation_enabled = True
    result = agent.execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    assert result["result"]["service_query"]["status"] == "empty"
    assert result["result"]["answer"]["summary"]
    assert result["state"]["specialist_reports"][0]["goal_id"] == "g_guide"
    events = sessions.events(run["id"], "user_alice")
    assert (
        sum(
            e["kind"] == "tool.completed"
            and e["payload"]["name"] == "query_service_requests"
            for e in events
        )
        == 1
    )


@pytest.mark.parametrize(
    "goal_status,worker",
    [("suspended", "guide"), ("withdrawn", "guide"), ("active", "policy")],
)
def test_invalid_goal_or_wrong_specialist_never_dispatches(
    agent_env, goal_status, worker
):
    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions))
    seed_goals(sessions, run, goal_status)
    gateway = WorkerFixture(worker)
    gateway.decisions[0]["goal_id"] = "g_guide"
    agent = runner(gateway)
    agent.delegation_enabled = True
    result = agent.execute(run["id"], "user_alice")
    assert not result["state"].get("specialist_reports")
    assert not any(
        e["kind"] == "specialist.delegated"
        for e in sessions.events(run["id"], "user_alice")
    )


def test_repeated_delegation_rejected_and_reused_evidence_reported(agent_env):
    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions))
    gateway = WorkerFixture()
    gateway.decisions.append(gateway.decisions[0].copy())
    gateway.decisions.appendleft(
        {
            "kind": "tool",
            "tool_name": "search_manuals",
            "arguments": {"query": "power"},
            "reason": "first retrieval",
        }
    )
    agent = runner(gateway)
    agent.delegation_enabled = True
    result = agent.execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    reports = result["state"]["specialist_reports"]
    assert len(reports) == 1 and reports[0]["evidence_ids"]
    assert reports[0]["calls"] == 1
