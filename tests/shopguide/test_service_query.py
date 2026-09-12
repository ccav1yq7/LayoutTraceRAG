import json

import pytest
from sqlalchemy import text
from test_m4 import (
    AgentFixtureGateway,
    SimulatedService,
    prepare_request,
    selected,
    submit,
)

pytest_plugins = ["test_m4", "test_m5_api"]


def test_confirm_submit_query_is_read_only_and_scoped(agent_env):
    repo, sessions, runner, _root = agent_env
    prepared = prepare_request(sessions, runner)
    proposal = prepared["result"]
    service = SimulatedService(sessions)
    service.approve(
        proposal["confirmation_id"],
        "user_alice",
        proposal["arguments_hash"],
        prepared["revision"],
    )
    created = runner().execute(prepared["id"], "user_alice")
    ticket = created["result"]["service_request"]["ticket_id"]
    session = sessions.get(prepared["session"], "user_alice")
    run = submit(sessions, session, "message_query", "查询工单")
    query = {
        "kind": "tool",
        "tool_name": "query_service_requests",
        "arguments": {"ticket_id": ticket},
        "reason": "read only",
    }
    result = runner(AgentFixtureGateway([query])).execute(run["id"], "user_alice")
    output = result["result"]["service_query"]
    assert result["status"] == "COMPLETED"
    assert output["simulated"] and output["tickets"][0]["ticket_id"] == ticket
    assert output["tickets"][0]["state"] == "created"
    assert (
        "answer" not in result["result"]
    )  # No manual-derived invented business status.
    assert service.query(run["id"], "user_alice")["tickets"] == output["tickets"]
    other = submit(sessions, selected(sessions, product=2))
    assert service.query(other["id"], "user_alice", ticket)["status"] == "empty"
    with pytest.raises(PermissionError):
        service.query(run["id"], "user_bob", ticket)
    assert service.query(run["id"], "user_alice", "ticket_unknown")["status"] == "empty"
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM sg_tickets")).scalar_one() == 1
        assert (
            c.execute(text("SELECT COUNT(*) FROM sg_confirmations")).scalar_one() == 1
        )


def test_list_limit_and_owner_filter(agent_env):
    _repo, sessions, _runner, _root = agent_env
    run = submit(sessions, selected(sessions))
    with sessions.transaction() as c:
        for i in range(23):
            c.execute(
                text("INSERT INTO sg_tickets VALUES (:id,:c,:p,:a)"),
                {
                    "id": f"ticket_{i:03}",
                    "c": f"confirmation_{i}",
                    "p": "user_bob" if i == 22 else "user_alice",
                    "a": json.dumps(
                        {"related_order_item": "item_1", "summary": "fixture"}
                    ),
                },
            )
    service = SimulatedService(sessions)
    result = service.query(run["id"], "user_alice")
    assert len(result["tickets"]) == 20 and result["has_more"]
    assert (
        service.query(run["id"], "user_alice", "ticket_021")["tickets"][0]["ticket_id"]
        == "ticket_021"
    )
    assert service.query(run["id"], "user_alice", "ticket_022")["status"] == "empty"


def test_demo_api_query_does_not_prepare_ticket(web_api):
    from test_m5_api import finish, send, session

    repo, _app, client, _other, _root = web_api
    s = session(client)
    started = send(client, s, "查询工单进度")
    assert started.status_code == 202
    result = finish(client, started.json()["run_id"])
    assert result["status"] == "COMPLETED"
    assert result["result"]["service_query"]["status"] == "empty"
    with repo.engine.connect() as c:
        assert (
            c.execute(text("SELECT COUNT(*) FROM sg_confirmations")).scalar_one() == 0
        )
