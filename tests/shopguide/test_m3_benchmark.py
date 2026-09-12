import json

import pytest
from pydantic import ValidationError
from test_m2 import tiny_archive

from shopguide.benchmarks.metrics import region_metrics
from shopguide.benchmarks.pm209 import PM209Adapter, PMPrediction
from shopguide.benchmarks.requests import export_requests
from shopguide.benchmarks.scoring import score_predictions
from shopguide.ingest.pipeline import import_corpus
from shopguide.ingest.pm209 import prepare_pm209
from shopguide.qa.contracts import PMRequest
from shopguide.qa.fixed import FixedRAG
from shopguide.qa.gateway import ExtractiveGateway
from shopguide.retrieval.models import HashEmbedder, RRFReranker
from shopguide.retrieval.store import ScopedIndex
from shopguide.storage.repository import Repository


@pytest.fixture
def benchmark(tmp_path):
    archive = tmp_path / "synthetic.zip"
    tiny_archive(archive)
    prepared = tmp_path / "prepared"
    prepare_pm209(archive, prepared)
    root = tmp_path / "runtime"
    import_corpus(
        prepared / "corpus", root, "snapshot_bench", "user_fixture", HashEmbedder()
    )
    repo = Repository(root / "metadata.db")
    index = ScopedIndex(
        repo, root / "assets", root / "indexes", "snapshot_bench", HashEmbedder()
    )
    adapter = PM209Adapter(
        FixedRAG(index, RRFReranker(), ExtractiveGateway()), "user_fixture"
    )
    yield prepared, adapter, tmp_path
    repo.close()


@pytest.mark.parametrize(
    "profile", ["pm209-given-page", "pm209-retrieved-top1", "pm209-multipage"]
)
def test_three_profiles_roundtrip_no_gold_in_predictor(benchmark, profile):
    prepared, adapter, tmp_path = benchmark
    requests = tmp_path / "requests.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    export_requests(prepared / "eval_private", "val", profile, requests)
    request_text = requests.read_text()
    assert (
        "PRIVATE_ANSWER_SENTINEL" not in request_text and "relevant" not in request_text
    )
    request = PMRequest.model_validate_json(request_text)
    assert (request.given_page_id is not None) == (profile == "pm209-given-page")
    adapter.predict_file(requests, predictions)
    prediction = PMPrediction.model_validate_json(predictions.read_text())
    assert prediction.status == "answered" and prediction.predicted_region_ids
    assert len(prediction.visited_page_ids) == 1
    assert len(list((tmp_path / "predictions.jsonl.runs").glob("*.json"))) == 1
    report = score_predictions(
        requests,
        predictions,
        prepared / "eval_private",
        prepared / "corpus",
        engineering=True,
    )
    assert report["sample_count"] == 1 and not report["failures"]
    assert report["official_compatible_region"]["instance_f1"] == 1
    assert report["official_benchmark"] is False and report["model_modes"] == ["fake"]
    assert report["text_scoring"]["status"] == "BLOCKED"
    with pytest.raises(RuntimeError, match="FORMAL_SCORING_BLOCKED"):
        score_predictions(
            requests, predictions, prepared / "eval_private", prepared / "corpus"
        )


def test_missing_predictions_preserve_denominator(benchmark):
    prepared, _adapter, tmp_path = benchmark
    requests = tmp_path / "requests.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    export_requests(prepared / "eval_private", "val", "pm209-retrieved-top1", requests)
    predictions.write_text("")
    report = score_predictions(
        requests,
        predictions,
        prepared / "eval_private",
        prepared / "corpus",
        engineering=True,
    )
    assert report["sample_count"] == 1 and report["prediction_count"] == 0
    assert report["failures"][0]["code"] == "MISSING_PREDICTION"
    assert report["extension_strict_region"]["instance_f1"] == 0


@pytest.mark.parametrize(
    "corruption",
    ["unknown_region", "duplicate_question", "wrong_given_info", "duplicate_region"],
)
def test_invalid_predictions_are_not_silently_filtered(benchmark, corruption):
    prepared, adapter, tmp_path = benchmark
    requests = tmp_path / "requests.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    export_requests(prepared / "eval_private", "val", "pm209-given-page", requests)
    adapter.predict_file(requests, predictions)
    record = json.loads(predictions.read_text())
    if corruption == "unknown_region":
        record["predicted_region_ids"] = ["region_unknown"]
    if corruption == "duplicate_region":
        record["predicted_region_ids"] *= 2
    if corruption == "wrong_given_info":
        record["given_information"]["page"] = False
    predictions.write_text(json.dumps(record) + "\n")
    if corruption == "duplicate_question":
        predictions.write_text(predictions.read_text() * 2)
    with pytest.raises(ValueError):
        score_predictions(
            requests,
            predictions,
            prepared / "eval_private",
            prepared / "corpus",
            engineering=True,
        )


def test_request_whitelist_rejects_gold_fields():
    base = {
        "question_id": "question_one",
        "manual_id": "manual_one",
        "question": "Q",
        "split": "val",
        "protocol": "pm209-retrieved-top1",
    }
    assert PMRequest.model_validate(base | {"question": " Q "}).question == " Q "
    for extra in (
        {"answer": "SECRET"},
        {"qa_data": []},
        {"given_page_id": "page_gold"},
        {"principal_id": "user_other"},
    ):
        with pytest.raises(ValidationError):
            PMRequest.model_validate(base | extra)


def test_compatible_and_strict_region_semantics():
    assert region_metrics([[1, 1]], [[1]], [[1, 2]])["instance_f1"] == 1
    assert region_metrics([[2]], [[1]], [[1, 2]])["instance_f1"] == 0
    assert region_metrics([[]], [[1]], [[1]])["all_recall"] == 0
    # Official candidate-universe behavior can omit gold from another page.
    compatible = region_metrics([[2]], [[1, 2]], [[2]])
    strict = region_metrics([[2]], [[1, 2]], [[1, 2]])
    assert compatible["all_recall"] == 1 and strict["all_recall"] == 0.5


def test_predictor_runs_with_private_directory_blocked(benchmark, monkeypatch):
    from pathlib import Path

    prepared, adapter, tmp_path = benchmark
    requests = tmp_path / "requests.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    export_requests(prepared / "eval_private", "val", "pm209-retrieved-top1", requests)
    private = (prepared / "eval_private").resolve()
    original = Path.open

    def deny_private(path, *args, **kwargs):
        if path.resolve().is_relative_to(private):
            raise AssertionError("predictor attempted to read private gold")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_private)
    adapter.predict_file(requests, predictions)
    prediction = PMPrediction.model_validate_json(predictions.read_text())
    assert prediction.status == "answered"
    assert "PRIVATE_ANSWER_SENTINEL" not in predictions.read_text()
