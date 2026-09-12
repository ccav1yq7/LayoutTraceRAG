import json
from pathlib import Path

import pytest

from shopguide.intent.candidates import CandidateRetriever, digest
from shopguide.intent.service import Taxonomy, empty_state, understand
from shopguide.qa.gateway import GatewayReply
from shopguide.retrieval.models import HashEmbedder, RRFReranker
from shopguide.schemas import Cost

TAX = Taxonomy.model_validate_json(
    Path("shopguide/intent/taxonomy.json").read_text()
)


def test_raw_negation_slots_and_multi_goal_recall_are_preserved(tmp_path):
    retriever = CandidateRetriever(TAX, HashEmbedder(), RRFReranker())
    state = empty_state("scope", TAX.version)
    state["goals"]["g1"] = {"intent_id": "service.apply", "status": "suspended"}
    raw = "先别申请，查询工单 ticket_demo；另外怎么安装 TEST-DS？"
    result = retriever.retrieve(raw, state)
    assert result["raw_query"] == raw
    assert result["bert"]["enabled"] is False
    assert result["manifest"]["embedding"]["model_mode"] == "fake"
    assert "service.apply" in result["protected_intents"]
    assert set(result["judge_intent_ids"]) == {d.intent_id for d in TAX.definitions}
    for hit in result["signals"]["service.apply"]["hits"]:
        assert raw[hit["start"] : hit["end"]] == hit["quote"]
    assert any(
        h["kind"] == "negative" for h in result["signals"]["service.apply"]["hits"]
    )
    assert {"ticket", "model"} <= {s["name"] for s in result["slots"]}
    retriever.export(tmp_path / "index.json")
    exported = json.loads((tmp_path / "index.json").read_text())
    sha = exported.pop("sha256")
    assert sha == digest(exported)
    assert all(
        r["example_id"].startswith("intent_example_") for r in exported["records"]
    )


def test_bad_vectors_and_wrong_rules_fail_explicitly(tmp_path):
    class Bad(HashEmbedder):
        def embed_documents(self, texts):
            return [[float("nan")] * 32 for t in texts]

    with pytest.raises(ValueError, match="VECTOR"):
        CandidateRetriever(TAX, Bad())
    rules = {"taxonomy_version": "wrong", "rules": {}}
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(rules))
    with pytest.raises(ValueError, match="VERSION"):
        CandidateRetriever(TAX, HashEmbedder(), rules_path=p)


def test_invalid_reranker_is_not_silently_ignored():
    class Bad(RRFReranker):
        def scores(self, *args):
            return [float("nan")]

    with pytest.raises(ValueError, match="RERANK"):
        CandidateRetriever(TAX, HashEmbedder(), Bad()).retrieve(
            "怎么安装", empty_state("s", TAX.version)
        )


def test_candidate_evidence_uses_existing_judge_call_without_state_rewrite():
    calls = []

    class Gateway:
        def complete(self, role, request, images, schema):
            calls.append(request)
            assert (
                role == "intent"
                and request["candidate_evidence"]["bert"]["enabled"] is False
            )
            return GatewayReply({"updates": [], "ambiguities": []}, Cost())

    state = empty_state("s", TAX.version)
    candidates = CandidateRetriever(TAX, HashEmbedder()).retrieve("不用了", state)
    result = understand(
        Gateway(),
        TAX,
        state,
        scope="s",
        turn_id="t1",
        message="不用了",
        history=[{"role": "assistant", "text": "需要再解释吗？"}],
        candidates=candidates,
    )
    assert len(calls) == 1 and result["goals"] == {} and state["revision"] == 0


def test_agent_candidate_stage_is_persisted_and_uses_one_judge(agent_env):
    from test_m4 import AgentFixtureGateway, selected, submit

    class CandidateFixture(AgentFixtureGateway):
        def complete(self, role, request, images, schema):
            if role == "intent":
                assert (
                    request["candidate_evidence"]["manifest"]["embedding"]["model_mode"]
                    == "fake"
                )
                return GatewayReply({"updates": [], "ambiguities": []}, Cost())
            return super().complete(role, request, images, schema)

    _repo, sessions, runner, _root = agent_env
    run = submit(sessions, selected(sessions))
    result = runner(
        CandidateFixture(), intent_enabled=True, intent_candidates_enabled=True
    ).execute(run["id"], "user_alice")
    assert result["status"] == "COMPLETED"
    assert result["state"]["intent_candidates"]["bert"]["enabled"] is False
    events = sessions.events(run["id"], "user_alice")
    assert (
        sum(
            e["kind"] == "model.started" and e["payload"]["role"] == "intent"
            for e in events
        )
        == 1
    )


pytest_plugins = ["test_m4"]
