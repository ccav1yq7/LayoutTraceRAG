"""Coverage fixes must preserve refusal, provenance and semantic verification."""

import json

import pytest
from test_m3 import RecordingGateway

from shopguide.qa.fixed import FixedRAG
from shopguide.retrieval.models import RRFReranker

pytest_plugins = ["test_m3", "test_m3_benchmark"]


def test_model_receives_explicit_image_ownership_and_selection_allowlist(qa):
    _repo, index, scope, _evidence = qa
    gateway = RecordingGateway()
    result = FixedRAG(index, RRFReranker(), gateway).run("power", scope)
    assert result["status"] == "completed"
    select = next(call for call in gateway.calls if call[0] == "select")
    assert set(select[1]["allowed_asset_ids"]) == set(select[2])
    assert all(set(e["asset_ids"]) <= set(select[2]) for e in select[1]["evidence"])
    writer = next(call for call in gateway.calls if call[0] == "write")
    mapping = writer[1]["display_asset_evidence"]
    assert set(mapping) == set(writer[2])
    for asset, refs in mapping.items():
        assert refs
        assert all(
            any(
                e["evidence_id"] == eid and asset in e["asset_ids"]
                for e in writer[1]["evidence"]
            )
            for eid in refs
        )


def test_explicit_image_selection_gets_registered_owner_and_still_verifies(qa):
    _repo, index, scope, _evidence = qa

    def mutate(role, reply, request):
        if role == "write":
            step = reply.payload["steps"][0]
            text_id = step["evidence_ids"][0]
            asset = next(
                a
                for a in request["allowed_display_asset_ids"]
                if not any(
                    e["evidence_id"] == text_id and a in e["asset_ids"]
                    for e in request["evidence"]
                )
            )
            step["evidence_ids"] = [text_id]
            step["asset_ids"] = [asset]

    gateway = RecordingGateway(mutate)
    result = FixedRAG(index, RRFReranker(), gateway).run("power", scope)
    assert result["status"] == "completed"
    assert result["image_reference_bindings"]
    assert [c[0] for c in gateway.calls] == ["select", "write", "verify"]
    binding = result["image_reference_bindings"][0]
    assert binding["added_evidence_ids"]
    verify = gateway.calls[-1][1]
    step = verify["answer"]["steps"][0]
    assert set(binding["added_evidence_ids"]) <= set(step["evidence_ids"])
    from shopguide.schemas import Evidence

    for aid in step["display_asset_ids"]:
        assert any(
            aid in index.repository.get(Evidence, eid).asset_ids
            for eid in step["evidence_ids"]
        )

    # A direct answer with the derived source references removed must still be rejected.
    from shopguide.evidence.renderer import render_answer
    from shopguide.schemas import GuideAnswer
    from shopguide.storage.repository import AssetRepository

    answer = GuideAnswer.model_validate_json(json.dumps(verify["answer"]))
    bad_step = answer.steps[0].model_copy(
        update={
            "evidence_ids": tuple(
                eid
                for eid in step["evidence_ids"]
                if eid not in binding["added_evidence_ids"]
            )
        }
    )
    malformed = answer.model_copy(update={"steps": (bad_step,)})
    lookup = {
        eid: index.repository.get(Evidence, eid)
        for c in answer.citations
        for eid in [c.evidence_id]
    }
    with pytest.raises(ValueError, match="display assets not supported"):
        render_answer(
            malformed,
            scope,
            lookup,
            AssetRepository(index.repository, index.asset_root),
        )


def test_schema_diagnostic_has_paths_not_private_output(qa):
    _repo, index, scope, _evidence = qa

    def mutate(role, reply, request):
        if role == "write":
            reply.payload["steps"] = "PRIVATE_RESPONSE_SENTINEL"

    result = FixedRAG(
        index, RRFReranker(), RecordingGateway(mutate), baseline="B1"
    ).run("power", scope)
    d = result["failure_detail"]
    assert d["stage"] == "write" and d["category"] == "schema_validation"
    assert any(e["loc"] == ["steps"] for e in d["validation_errors"])
    assert "PRIVATE_RESPONSE_SENTINEL" not in json.dumps(result)


def test_legitimate_refusal_not_promoted_to_answer(qa):
    _repo, index, scope, _evidence = qa

    def mutate(role, reply, request):
        if role == "write":
            reply.payload = {
                "status": "abstained",
                "summary": "Insufficient evidence",
                "summary_evidence_ids": [],
                "prerequisites": [],
                "steps": [],
                "unresolved_items": [],
            }

    result = FixedRAG(
        index, RRFReranker(), RecordingGateway(mutate), baseline="B1"
    ).run("power", scope)
    assert result["answer"]["status"] == "abstained" and not result["answer"]["steps"]
    assert result["error"] == "WRITER_ABSTAINED"
    assert result["failure_detail"]["category"] == "abstention"


def test_benchmark_refusal_stays_out_of_coverage(benchmark):
    from shopguide.benchmarks.pm209 import PMPrediction
    from shopguide.benchmarks.requests import export_requests
    from shopguide.benchmarks.scoring import score_predictions

    prepared, adapter, root = benchmark

    def mutate(role, reply, request):
        if role == "write":
            reply.payload = {
                "status": "abstained",
                "summary": "Insufficient evidence",
                "summary_evidence_ids": [],
                "prerequisites": [],
                "steps": [],
                "unresolved_items": [],
            }

    adapter.rag.gateway = RecordingGateway(mutate)
    req = root / "requests-refusal.jsonl"
    pred = root / "predictions-refusal.jsonl"
    export_requests(prepared / "eval_private", "val", "pm209-given-page", req)
    adapter.predict_file(req, pred)
    item = PMPrediction.model_validate_json(pred.read_text())
    assert (
        item.status == "abstained"
        and not item.answer_text
        and not item.predicted_region_ids
    )
    report = score_predictions(
        req, pred, prepared / "eval_private", prepared / "corpus", engineering=True
    )
    assert report["sample_count"] == 1 and len(report["failures"]) == 1
    assert report["extension_strict_region"]["instance_f1"] == 0


def test_derived_image_reference_does_not_override_negative_verifier(qa):
    _repo, index, scope, _evidence = qa

    def mutate(role, reply, request):
        if role == "write":
            step = reply.payload["steps"][0]
            text_id = step["evidence_ids"][0]
            asset = next(
                a
                for a in request["allowed_display_asset_ids"]
                if not any(
                    e["evidence_id"] == text_id and a in e["asset_ids"]
                    for e in request["evidence"]
                )
            )
            step["evidence_ids"] = [text_id]
            step["asset_ids"] = [asset]
        if role == "verify":
            reply.payload["images"][0]["supported"] = False

    result = FixedRAG(index, RRFReranker(), RecordingGateway(mutate)).run(
        "power", scope
    )
    assert result["error"] == "UNSUPPORTED_ANSWER" and not result["answer"]["steps"]
    assert result["image_reference_bindings"]


def test_complete_false_with_full_coverage_remains_rejected_and_distinguished(qa):
    _repo, index, scope, _evidence = qa

    def mutate(role, reply, request):
        if role == "verify":
            reply.payload["complete"] = False

    result = FixedRAG(index, RRFReranker(), RecordingGateway(mutate)).run(
        "power", scope
    )
    assert result["status"] == "error" and not result["answer"]["steps"]
    detail = result["failure_detail"]
    assert detail["reason"] == "VERIFIER_DECLARED_INCOMPLETE"
    assert (
        detail["verification_coverage"]["claim_ids_match"]
        and detail["verification_coverage"]["image_pairs_match"]
    )


def test_ambiguous_image_owner_is_not_invented(qa):
    from shopguide.qa.contracts import WriterDraft
    from shopguide.qa.gateway import ExtractiveGateway
    from shopguide.schemas import Evidence

    _repo, index, scope, _evidence = qa
    gateway = RecordingGateway()
    rag = FixedRAG(index, RRFReranker(), gateway)
    rag.run("power", scope)
    _, request, images = gateway.calls[1]
    payload = ExtractiveGateway().complete("write", request, images, {}).payload
    draft = WriterDraft.model_validate_json(json.dumps(payload))
    lookup = {
        e["evidence_id"]: index.repository.get(Evidence, e["evidence_id"])
        for e in request["evidence"]
    }
    asset = draft.steps[0].asset_ids[0]
    owner = next(e for e in lookup.values() if asset in e.asset_ids)
    lookup["ev_duplicate_owner"] = owner.model_copy(
        update={"evidence_id": "ev_duplicate_owner"}
    )
    from shopguide.qa.fixed import PipelineFailure

    with pytest.raises(PipelineFailure, match="IMAGE_EVIDENCE_OWNER_INVALID"):
        rag._assemble(draft, scope, lookup, images)


@pytest.mark.parametrize(
    "message,reason",
    [
        ("MODEL_HTTP_400", "MODEL_HTTP_400"),
        ("MODEL_HTTP_400 PRIVATE_SENTINEL", "VALIDATION_OR_DEPENDENCY_FAILED"),
    ],
)
def test_gateway_status_diagnostic_is_specific_without_raw_error_leak(
    qa, message, reason
):
    _repo, index, scope, _evidence = qa

    class Broken(RecordingGateway):
        def complete(self, *args):
            raise RuntimeError(message)

    result = FixedRAG(index, RRFReranker(), Broken(), baseline="B1").run("power", scope)
    assert result["status"] == "error" and result["failure_detail"]["reason"] == reason
    assert "PRIVATE_SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize(
    "reason", ["MODEL_TIMEOUT", "MODEL_OUTPUT_TRUNCATED", "MODEL_OUTPUT_INVALID_JSON"]
)
def test_model_failure_reason_survives_pipeline(qa, reason):
    _repo, index, scope, _evidence = qa

    class FailureGateway(RecordingGateway):
        def complete(self, *args, **kwargs):
            raise RuntimeError(reason)

    result = FixedRAG(index, RRFReranker(), FailureGateway()).run("power", scope)
    assert result["status"] == "error"
    assert result["failure_detail"]["reason"] == reason


@pytest.mark.parametrize("draft_status", ["answered", "partial"])
def test_no_optional_image_preserves_content_status(qa, draft_status):
    _repo, index, scope, _evidence = qa

    def mutate(role, reply, request):
        if role == "select":
            reply.payload["asset_ids"] = []
        if role == "write":
            reply.payload["status"] = draft_status

    result = FixedRAG(index, RRFReranker(), RecordingGateway(mutate)).run(
        "What should I do?", scope
    )
    assert result["status"] == "completed"
    assert result["answer"]["status"] == draft_status
    assert not result["answer"]["unresolved_items"]


def test_service_error_does_not_ask_customer_for_unneeded_information(qa):
    _repo, index, scope, _evidence = qa

    class Broken(RecordingGateway):
        def complete(self, *args, **kwargs):
            raise RuntimeError("MODEL_OUTPUT_INVALID_JSON")

    result = FixedRAG(index, RRFReranker(), Broken()).run("power", scope)
    assert result["failure_detail"]["reason"] == "MODEL_OUTPUT_INVALID_JSON"
    assert result["answer"]["followup_question"] is None
    assert result["answer"]["unresolved_items"] == []
    assert "MODEL_" not in json.dumps(result["answer"])
    assert not result["answer"]["steps"]


@pytest.mark.parametrize("status,tickets", [("empty", []), ("ok", ["ticket_test"])])
def test_business_context_reaches_writer_and_verifier_without_becoming_evidence(
    qa, status, tickets
):
    _repo, index, scope, _evidence = qa
    gateway = RecordingGateway()
    context = {"status": status, "ticket_ids": tickets, "has_more": False}
    result = FixedRAG(index, RRFReranker(), gateway).run(
        "启动方法和工单进度", scope, delivered_service_query=context
    )
    assert result["status"] == "completed"
    for role, request, _images in gateway.calls:
        if role in ("write", "verify"):
            actual = request["separately_delivered_service_query"]
            assert actual["status"] == status and actual["ticket_ids"] == tickets
            assert actual["source_tool"] == "query_service_requests"
            assert not any(
                e["evidence_id"].startswith("ticket_") for e in request["evidence"]
            )
    assert not any(
        c["evidence_id"].startswith("ticket_") for c in result["answer"]["citations"]
    )


def test_business_context_never_overrides_failed_verification(qa):
    _repo, index, scope, _evidence = qa

    def mutate(role, reply, request):
        if role == "verify":
            reply.payload["complete"] = False

    result = FixedRAG(index, RRFReranker(), RecordingGateway(mutate)).run(
        "启动方法和工单进度", scope, delivered_service_query={"status": "empty"}
    )
    assert result["status"] == "error"
    assert result["failure_detail"]["reason"] == "VERIFIER_DECLARED_INCOMPLETE"


def test_invalid_business_context_is_rejected_before_model_call(qa):
    _repo, index, scope, _evidence = qa
    gateway = RecordingGateway()
    result = FixedRAG(index, RRFReranker(), gateway).run(
        "power",
        scope,
        delivered_service_query={"status": "empty", "ticket_ids": ["ticket_test"]},
    )
    assert result["status"] == "error" and gateway.calls == []
