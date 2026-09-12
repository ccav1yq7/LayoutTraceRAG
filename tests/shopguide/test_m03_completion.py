"""Offline contracts only. Synthetic records here are not benchmark results."""

import json
from pathlib import Path

import pytest

from shopguide.benchmarks.campaign import RequestBudget
from shopguide.benchmarks.official import sha, validate_evaluation
from shopguide.benchmarks.pm209 import PMPrediction
from shopguide.benchmarks.requests import export_requests
from shopguide.benchmarks.scoring import score_predictions
from shopguide.ingest.pipeline import import_corpus
from shopguide.retrieval.models import (
    BGEEmbedder,
    BGEReranker,
    HashEmbedder,
)

pytest_plugins = ["test_m3_benchmark"]


@pytest.fixture
def recorded(benchmark):
    prepared, adapter, root = benchmark
    requests, predictions = root / "requests.jsonl", root / "predictions.jsonl"
    export_requests(prepared / "eval_private", "val", "pm209-given-page", requests)
    adapter.predict_file(requests, predictions)
    prediction = PMPrediction.model_validate_json(predictions.read_text())
    run = (
        predictions.parent
        / (predictions.name + ".runs")
        / (prediction.run_id + ".json")
    )
    record = json.loads(run.read_text())
    # Transport-contract fixture; no actual model or claimed effect measurements.
    record["configuration"]["embedding"]["model_mode"] = "real"
    record["configuration"]["reranker"]["model_mode"] = "real"
    record["component_modes"] = dict.fromkeys(
        ("embedding", "reranker", "gateway"), "real"
    )
    import hashlib

    digest = hashlib.sha256(
        json.dumps(record["configuration"], sort_keys=True).encode()
    ).hexdigest()
    prediction = prediction.model_copy(
        update={"model_mode": "real", "configuration_sha256": digest}
    )
    run.write_text(json.dumps(record))
    predictions.write_text(prediction.model_dump_json() + "\n")
    evaluation = root / "evaluation.json"
    evaluation.write_text(
        json.dumps(
            {
                "run_kind": "real_baseline_validation",
                "split": "val",
                "protocol": prediction.protocol,
                "baseline": prediction.baseline,
                "snapshot_id": prediction.snapshot_id,
                "model_version_policy": "synthetic transport contract, not effect evidence",
                "requests_sha256": sha(requests),
                "private_split_sha256": sha(prepared / "eval_private/val.jsonl"),
                "corpus_manifest_sha256": sha(prepared / "corpus/manifest.json"),
                "configuration": record["configuration"],
                "reference_lock_sha256": "0" * 64,
            }
        )
    )
    return prepared, requests, predictions, evaluation, prediction, run


def test_frozen_run_and_source_mapping(recorded):
    prepared, requests, predictions, evaluation, prediction, _run = recorded
    assert (
        validate_evaluation(
            evaluation,
            requests,
            predictions,
            prepared / "eval_private",
            prepared / "corpus",
            [prediction],
        )["split"]
        == "val"
    )


@pytest.mark.parametrize(
    "change",
    [
        "fake",
        "text",
        "regions",
        "images",
        "pages",
        "usage",
        "config",
        "schedule",
        "gold",
        "profile",
    ],
)
def test_formal_evidence_tampering_rejected(recorded, change):
    prepared, requests, predictions, evaluation, prediction, run = recorded
    if change == "fake":
        prediction = prediction.model_copy(update={"model_mode": "fake"})
    elif change == "text":
        prediction = prediction.model_copy(
            update={"answer_text": "forged perfect answer"}
        )
    elif change == "regions":
        prediction = prediction.model_copy(update={"predicted_region_ids": ()})
    elif change == "images":
        prediction = prediction.model_copy(
            update={"display_asset_ids": ("asset_forged",)}
        )
    elif change == "pages":
        prediction = prediction.model_copy(
            update={"visited_page_ids": ("page_forged",)}
        )
    elif change == "usage":
        prediction = prediction.model_copy(update={"model_calls": 0})
    elif change == "config":
        r = json.loads(run.read_text())
        r["configuration"]["model_id"] = "changed"
        run.write_text(json.dumps(r))
    elif change == "schedule":
        requests.write_text(requests.read_text() + "\n")
    elif change == "gold":
        p = prepared / "eval_private/val.jsonl"
        p.write_text(p.read_text() + "\n")
    elif change == "profile":
        prediction = prediction.model_copy(update={"protocol": "pm209-multipage"})
    with pytest.raises(ValueError):
        validate_evaluation(
            evaluation,
            requests,
            predictions,
            prepared / "eval_private",
            prepared / "corpus",
            [prediction],
        )


def test_reference_failure_does_not_publish_partial_official_report(
    recorded, monkeypatch
):
    prepared, requests, predictions, evaluation, _prediction, _run = recorded
    lock = evaluation.parent / "reference.lock.json"
    lock.write_text("{}")
    m = json.loads(evaluation.read_text())
    m["reference_lock_sha256"] = sha(lock)
    evaluation.write_text(json.dumps(m))

    def failure(*args, **kwargs):
        raise RuntimeError("REFERENCE_FAILED")

    monkeypatch.setattr(
        "shopguide.benchmarks.official.run_reference", failure
    )
    with pytest.raises(RuntimeError, match="REFERENCE_FAILED"):
        score_predictions(
            requests,
            predictions,
            prepared / "eval_private",
            prepared / "corpus",
            evaluation_manifest=evaluation,
            reference_python=Path("python"),
            reference_source=Path("source"),
            reference_lock=lock,
        )


def test_request_budget_counts_failed_attempts_before_transport(tmp_path):
    class BrokenGateway:
        calls = 0

        def complete(self, *args):
            self.calls += 1
            raise RuntimeError("transport failed")

    gateway = BrokenGateway()
    journal = tmp_path / "requests.jsonl"
    budget = RequestBudget(gateway, 1, journal)
    with pytest.raises(RuntimeError, match="transport failed"):
        budget.complete("write", {}, {}, {})
    with pytest.raises(RuntimeError, match="BUDGET_EXHAUSTED"):
        budget.complete("write", {}, {}, {})
    assert gateway.calls == 1 and budget.used == 1
    assert len(journal.read_text().splitlines()) == 1


@pytest.mark.parametrize("model", [BGEEmbedder, BGEReranker])
def test_bge_rejects_unpinned_revision_and_invalid_resource_limits_before_loading(
    model,
):
    for kwargs in (
        {"revision": "main"},
        {"revision": "a" * 40, "batch_size": 0},
        {"revision": "a" * 40, "max_length": 9000},
    ):
        with pytest.raises(ValueError):
            model("BAAI/test", **kwargs)


def test_manual_subset_rejects_missing_manual_without_creating_runtime(benchmark):
    prepared, _adapter, root = benchmark
    target = root / "bad-runtime"
    with pytest.raises(ValueError, match="manual selection"):
        import_corpus(
            prepared / "corpus",
            target,
            "snapshot_subset",
            "user_fixture",
            HashEmbedder(),
            manual_ids=["manual_missing"],
        )
    assert not (target / "metadata.db").exists()


def test_scope_query_count_is_bounded_and_revocation_is_live(tmp_path):
    from sqlalchemy import event

    from shopguide.schemas import DocumentVersion, Product, ProductVariant
    from shopguide.storage.repository import Repository

    repo = Repository(tmp_path / "metadata.db")
    try:
        p = Product(
            product_id="product_many",
            domain="demo",
            brand="test",
            model="test",
            category="fixture",
        )
        v = ProductVariant(variant_id="variant_many", product_id=p.product_id)
        repo.put(p, p.product_id)
        repo.put(v, v.variant_id)
        for i in range(20):
            doc = DocumentVersion(
                doc_version_id=f"doc_{i}",
                source_id=f"source_{i}",
                content_sha256="a" * 64,
                language="en",
                version_label="fixture",
                status="active",
            )
            repo.put(doc, doc.doc_version_id)
            repo.grant(
                principal="user_a",
                product=p.product_id,
                variant=v.variant_id,
                document=doc.doc_version_id,
                snapshot="snapshot_many",
                basis="synthetic scope scaling test",
            )
        queries = []

        def count(*args):
            queries.append(args[2])

        event.listen(repo.engine, "before_cursor_execute", count)
        scope = repo.scope("user_a", p.product_id, v.variant_id, "snapshot_many")
        assert len(scope.allowed_doc_version_ids) == 20 and len(queries) <= 2
        repo.revoke("doc_0")
        with pytest.raises(PermissionError):
            repo.authorize(scope)
        assert (
            len(
                repo.scope(
                    "user_a", p.product_id, v.variant_id, "snapshot_many"
                ).allowed_doc_version_ids
            )
            == 19
        )
    finally:
        repo.close()


def test_retrieval_uses_official_page_weighting_and_retains_missing_denominator():
    from shopguide.benchmarks.retrieval import retrieval_metrics

    expected = {"q1": ("m1", "p1"), "q2": ("m1", "p2"), "q3": ("m2", "p3")}
    rows = [
        {"question_id": "q1", "manual_id": "m1", "retrieved_page_ids": ["p1"]},
        {"question_id": "q3", "manual_id": "m2", "retrieved_page_ids": ["p3"]},
    ]
    result = retrieval_metrics(rows, expected, {"m1": 2, "m2": 6})
    assert result["upstream_compatible_qa2page"]["qa2page_r1"] == 0.875
    assert result["diagnostic_question_micro"]["recall@1"] == 2 / 3
    assert result["missing_predictions"] == ["q2"] and result["sample_count"] == 3


def test_formal_metric_report_keeps_validation_label(recorded, monkeypatch):
    prepared, requests, predictions, evaluation, _prediction, _run = recorded
    lock = evaluation.parent / "ref.json"
    lock.write_text("{}")
    m = json.loads(evaluation.read_text())
    m["reference_lock_sha256"] = sha(lock)
    evaluation.write_text(json.dumps(m))
    from shopguide.benchmarks.metrics import region_metrics

    def reference(items, **kwargs):
        return {
            "sample_count": len(items),
            "text": {"Bleu_1": 0.1},
            "region": region_metrics(
                [i["pred_regions"] for i in items],
                [i["gt_regions"] for i in items],
                [i["all_regions"] for i in items],
            ),
            "by_region_type": {},
        }

    monkeypatch.setattr(
        "shopguide.benchmarks.official.run_reference", reference
    )
    report = score_predictions(
        requests,
        predictions,
        prepared / "eval_private",
        prepared / "corpus",
        evaluation_manifest=evaluation,
        reference_python=Path("python"),
        reference_source=Path("source"),
        reference_lock=lock,
    )
    assert (
        report["report_kind"] == "real_baseline_validation"
        and report["official_benchmark"] is False
    )
    assert report["text_scoring"]["status"] == "COMPLETE"
    assert report["sample_count"] == 1 and report["coverage"] == 1


@pytest.mark.parametrize("failure", ["incomplete", "bad_status"])
def test_partial_http_response_is_bounded_and_does_not_leak_payload(failure):
    import http.client

    from shopguide.models.responses import ResponsesGateway

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            if failure == "incomplete":
                raise http.client.IncompleteRead(b"PRIVATE_RESPONSE_SENTINEL")
            raise http.client.BadStatusLine("PRIVATE_RESPONSE_SENTINEL")

    class Opener:
        calls = 0

        def open(self, request, timeout):
            self.calls += 1
            return Response()

    opener = Opener()
    gateway = ResponsesGateway(
        "test-real-transport", "https://example.invalid", "fixture-key", opener=opener
    )
    with pytest.raises(RuntimeError, match="^MODEL_HTTP_PROTOCOL_ERROR$"):
        gateway.complete("write", {}, {}, {"type": "object", "properties": {}})
    assert opener.calls == 1
