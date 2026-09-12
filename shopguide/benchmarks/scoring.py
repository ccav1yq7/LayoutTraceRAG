"""Evaluation-only scoring. Never import this module into a predictor worker."""

import hashlib
import json
from pathlib import Path

from ..ingest.pm209 import read_corpus
from ..qa.contracts import PMRequest
from .metrics import region_metrics
from .pm209 import PMPrediction
from .requests import question_id


def score_predictions(
    requests_path: Path,
    predictions_path: Path,
    private: Path,
    corpus: Path,
    *,
    engineering=False,
    evaluation_manifest: Path | None = None,
    reference_python: Path | None = None,
    reference_source: Path | None = None,
    reference_lock: Path | None = None,
    reference_java_home: Path | None = None,
):
    requests = [
        PMRequest.model_validate_json(l)
        for l in requests_path.read_text().splitlines()
        if l.strip()
    ]
    predictions = [
        PMPrediction.model_validate_json(l)
        for l in predictions_path.read_text().splitlines()
        if l.strip()
    ]
    if not requests or len({r.question_id for r in requests}) != len(requests):
        raise ValueError("empty/duplicate schedule")
    if len({p.question_id for p in predictions}) != len(predictions):
        raise ValueError("duplicate predictions")
    if (
        len({p.snapshot_id for p in predictions}) > 1
        or len({p.model_mode for p in predictions}) > 1
    ):
        raise ValueError("mixed snapshot/model modes cannot be aggregated")
    completed = [p for p in predictions if p.status in ("answered", "partial")]
    if (
        any(p.configuration_sha256 is None for p in completed)
        or len({p.configuration_sha256 for p in completed}) > 1
    ):
        raise ValueError("completed predictions require one frozen configuration")
    expected = {r.question_id: r for r in requests}
    byid = {p.question_id: p for p in predictions}
    if not byid.keys() <= expected.keys():
        raise ValueError("prediction not in schedule")
    if not engineering:
        if not all(
            (evaluation_manifest, reference_python, reference_source, reference_lock)
        ):
            raise RuntimeError(
                "FORMAL_SCORING_BLOCKED: frozen manifest and isolated reference required"
            )
        assert reference_lock is not None
        from .official import validate_evaluation

        evaluation = validate_evaluation(
            evaluation_manifest,
            requests_path,
            predictions_path,
            private,
            corpus,
            predictions,
        )
        if (
            evaluation["reference_lock_sha256"]
            != hashlib.sha256(reference_lock.read_bytes()).hexdigest()
        ):
            raise ValueError("REFERENCE_LOCK_CHANGED")
    protocols = {r.protocol for r in requests}
    splits = {r.split for r in requests}
    if (
        len(protocols) != 1
        or len(splits) != 1
        or len({p.baseline for p in predictions}) > 1
    ):
        raise ValueError("protocol/split/baseline results must not be merged")
    split = next(iter(splits))
    profile = next(iter(protocols))
    manifest = json.loads((private / "manifest.json").read_text())
    corpus_manifest = json.loads((corpus / "manifest.json").read_text())
    if (
        manifest.get("role") != "evaluation_private"
        or manifest["archive_sha256"] != corpus_manifest["source_archive_sha256"]
    ):
        raise ValueError("gold/corpus identity mismatch")
    page_map = {p.page_id: p for p, _image in read_corpus(corpus)}
    regions = {
        r.region_id: (p.page_id, p.manual_id)
        for p in page_map.values()
        for r in p.regions
    }
    gold_rows = {}
    for line in (private / f"{split}.jsonl").read_text().splitlines():
        row = json.loads(line)
        qid = question_id(manifest["archive_sha256"], split, row["question_index"])
        if qid in gold_rows:
            raise ValueError("duplicate gold question")
        gold_rows[qid] = row
    preds = []
    golds = []
    universes = []
    strict_universes = []
    errors = []
    reference_items = []
    manual_hits: dict[str, dict] = {}
    page_hits = {1: 0, 3: 0, 5: 0}
    for qid, request in expected.items():
        row = gold_rows[qid]
        if (
            row["manual_id"] != request.manual_id
            or row["qa"]["question"]["text"] != request.question
        ):
            raise ValueError("question/manual mapping mismatch")
        if request.given_page_id and request.given_page_id != row["page_id"]:
            raise ValueError("given-page mapping mismatch")
        mapping = {r["official_region_id"]: r["region_id"] for r in row["regions"]}
        gold = [mapping[r] for r in row["qa"]["answer"]["relevant"]]
        prediction = byid.get(qid)
        pred = []
        visited = []
        if prediction is None:
            errors.append({"question_id": qid, "code": "MISSING_PREDICTION"})
        else:
            if (
                prediction.manual_id != request.manual_id
                or prediction.protocol != profile
                or prediction.split != split
            ):
                raise ValueError("prediction identity mismatch")
            if prediction.given_information != {
                "manual": True,
                "page": request.given_page_id is not None,
            }:
                raise ValueError("given-information metadata mismatch")
            if (
                prediction.model_calls > 3
                or prediction.image_inputs > 8
                or len(prediction.visited_page_ids) > 4
            ):
                raise ValueError("prediction exceeds frozen engineering budget")
            if len(set(prediction.retrieved_page_ids)) != len(
                prediction.retrieved_page_ids
            ):
                raise ValueError("duplicate retrieved page IDs")
            visited = list(prediction.visited_page_ids)
            all_pages = [*prediction.retrieved_page_ids, *visited]
            if any(
                p not in page_map or page_map[p].manual_id != request.manual_id
                for p in all_pages
            ):
                raise ValueError("unknown/foreign predicted page")
            if len(set(visited)) != len(visited) or (
                profile != "pm209-multipage" and len(visited) > 1
            ):
                raise ValueError("invalid page budget")
            if profile == "pm209-given-page" and any(
                p != request.given_page_id for p in visited
            ):
                raise ValueError("given-page violation")
            if (
                profile == "pm209-retrieved-top1"
                and visited
                and (
                    not prediction.retrieved_page_ids
                    or visited != list(prediction.retrieved_page_ids[:1])
                )
            ):
                raise ValueError("top1 profile read a different page")
            if len(set(prediction.predicted_region_ids)) != len(
                prediction.predicted_region_ids
            ):
                raise ValueError("duplicate region predictions")
            if any(
                r not in regions
                or regions[r][1] != request.manual_id
                or regions[r][0] not in visited
                for r in prediction.predicted_region_ids
            ):
                raise ValueError("unknown/foreign/unvisited region prediction")
            if prediction.status in ("error", "abstained"):
                if prediction.predicted_region_ids or prediction.display_asset_ids:
                    raise ValueError("failed answer contains selections")
                errors.append(
                    {"question_id": qid, "code": prediction.error or "ABSTAINED"}
                )
            else:
                pred = list(prediction.predicted_region_ids)
            for k in page_hits:
                page_hits[k] += int(row["page_id"] in prediction.retrieved_page_ids[:k])
        # Official retrieved-page semantics use the selected page candidate universe.
        universe = [
            r.region_id for page_id in visited for r in page_map[page_id].regions
        ]
        if not visited:
            universe = [r["region_id"] for r in row["regions"]]
        preds.append(pred)
        golds.append(gold)
        universes.append(universe)
        strict_universes.append(list(set(gold) | set(pred)))
        region_kinds = {
            r.region_id: r.kind
            for page in page_map.values()
            if page.manual_id == request.manual_id
            for r in page.regions
        }
        reference_items.append(
            {
                "caption": prediction.answer_text
                if prediction and prediction.status in ("answered", "partial")
                else "",
                "gt": row["qa"]["answer"]["text"],
                "pred_regions": pred,
                "gt_regions": gold,
                "all_regions": universe,
                "gt_region_cls": [region_kinds[r] for r in gold],
                "all_region_cls": [region_kinds[r] for r in universe],
            }
        )
        counts = manual_hits.setdefault(
            request.manual_id, {"count": 0, "hits": {1: 0, 3: 0, 5: 0}}
        )
        counts["count"] += 1
        for k in (1, 3, 5):
            counts["hits"][k] += int(
                prediction is not None
                and row["page_id"] in prediction.retrieved_page_ids[:k]
            )
    report = {
        "report_kind": "engineering_diagnostic",
        "official_benchmark": False,
        "protocol": profile,
        "sample_count": len(requests),
        "prediction_count": len(predictions),
        "failures": errors,
        "model_modes": sorted({p.model_mode for p in predictions}),
        "official_compatible_region": region_metrics(preds, golds, universes),
        "extension_strict_region": region_metrics(preds, golds, strict_universes),
        "diagnostic_page_recall": None
        if profile == "pm209-given-page"
        else {f"recall@{k}": v / len(requests) for k, v in page_hits.items()},
        "text_scoring": {
            "status": "BLOCKED",
            "reason": "Full pinned NLGEval reference resources/parity not provisioned",
        },
        "note": "Synthetic/fake runs are engineering diagnostics only; multipage and strict scores are extensions.",
    }

    if not engineering:
        from .official import run_reference, sha

        reference = run_reference(
            reference_items,
            python=reference_python,
            source=reference_source,
            lock=reference_lock,
            java_home=reference_java_home,
        )
        for key, value in report["official_compatible_region"].items():
            if abs(reference["region"][key] - value) > 1e-12:
                raise ValueError("REGION_REFERENCE_PARITY_FAILED")
        from .retrieval import retrieval_metrics

        candidate_counts: dict[str, int] = {}
        for page in page_map.values():
            candidate_counts[page.manual_id] = (
                candidate_counts.get(page.manual_id, 0) + 1
            )
        retrieval = (
            retrieval_metrics(
                [p.model_dump(mode="json") for p in predictions],
                {
                    qid: (r.manual_id, gold_rows[qid]["page_id"])
                    for qid, r in expected.items()
                },
                candidate_counts,
            )
            if profile != "pm209-given-page"
            else None
        )
        report.update(
            {
                "report_kind": "real_baseline_validation",
                "official_benchmark": False,
                "official_metric_implementation": True,
                "reference": reference,
                "retrieval": retrieval,
                "text_scoring": {"status": "COMPLETE", "metrics": reference["text"]},
                "reference_lock_sha256": sha(reference_lock),
                "evaluation_manifest_sha256": sha(evaluation_manifest),
                "page_recall_manual_macro": None
                if profile == "pm209-given-page"
                else {
                    f"recall@{k}": sum(
                        m["hits"][k] / m["count"] for m in manual_hits.values()
                    )
                    / len(manual_hits)
                    for k in (1, 3, 5)
                },
                "manual_sample_counts": {m: v["count"] for m, v in manual_hits.items()},
                "coverage": len(completed) / len(requests),
                "note": "Pinned official metric functions on a registered ShopGuide validation schedule, not URA reproduction or full test acceptance; multipage is an extension. Missing predictions remain empty in the denominator. See reference lock for tokenizer/runtime adaptation.",
            }
        )
    return report
