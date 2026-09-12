"""Prediction-only adapter: no gold paths, loader or answer fields accepted."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..qa.contracts import PMRequest, Profile
from ..schemas import ID, SHA256, Contract, ProductVariant, new_id


class PMPrediction(Contract):
    schema_version: Literal[1] = 1
    dataset: Literal["pm209"] = "pm209"
    question_id: ID
    manual_id: ID
    split: Literal["train", "val", "test"]
    protocol: Profile
    baseline: Literal["B1", "B2"]
    snapshot_id: ID
    run_id: ID
    model_mode: Literal["real", "fake"]
    given_information: dict[str, bool]
    configuration_sha256: SHA256 | None = None
    retrieved_page_ids: tuple[ID, ...] = ()
    visited_page_ids: tuple[ID, ...] = ()
    answer_text: str
    predicted_region_ids: tuple[ID, ...] = ()
    display_asset_ids: tuple[ID, ...] = ()
    status: Literal["answered", "partial", "abstained", "error"]
    error: str | None = None
    model_calls: int = Field(ge=0)
    image_inputs: int = Field(ge=0)


class PM209Adapter:
    def __init__(self, rag, principal: str):
        self.rag = rag
        self.principal = principal

    def predict(self, request: PMRequest):
        request = PMRequest.model_validate_json(request.model_dump_json())
        run_id = new_id("run")
        base = {
            "question_id": request.question_id,
            "manual_id": request.manual_id,
            "split": request.split,
            "protocol": request.protocol,
            "baseline": self.rag.baseline,
            "snapshot_id": self.rag.index.snapshot,
            "run_id": run_id,
            "model_mode": self.rag.model_mode,
            "given_information": {
                "manual": True,
                "page": request.given_page_id is not None,
            },
        }
        try:
            repo = self.rag.index.repository
            variants = [
                v for v in repo.all(ProductVariant) if v.product_id == request.manual_id
            ]
            if len(variants) != 1:
                raise ValueError("manual variant unavailable or ambiguous")
            scope = repo.scope(
                self.principal,
                request.manual_id,
                variants[0].variant_id,
                self.rag.index.snapshot,
            )
            result = self.rag.run(
                request.question,
                scope,
                profile=request.protocol,
                given_page=request.given_page_id,
                run_id=run_id,
            )
            answer = result["answer"]
            text = "\n".join(
                [
                    answer["summary"],
                    *answer.get("prerequisites", []),
                    *(s["text"] for s in answer.get("steps", [])),
                ]
            )
            citations = answer.get("citations", [])
            regions = list(
                dict.fromkeys(
                    c["source_locator"]["region_id"]
                    for c in citations
                    if c["source_locator"].get("region_id")
                )
            )
            assets = list(
                dict.fromkeys(
                    a for s in answer.get("steps", []) for a in s["display_asset_ids"]
                )
            )
            prediction = PMPrediction(
                **base,
                configuration_sha256=result["configuration_sha256"],
                retrieved_page_ids=tuple(result["retrieved_page_ids"]),
                visited_page_ids=tuple(result["visited_page_ids"]),
                answer_text=text if result["status"] == "completed" else "",
                predicted_region_ids=tuple(regions),
                display_asset_ids=tuple(assets),
                status=answer["status"]
                if result["status"] == "completed"
                else "abstained"
                if result["error"] == "WRITER_ABSTAINED"
                else "error",
                error=result["error"],
                model_calls=result["usage"]["model_calls"],
                image_inputs=result["usage"]["image_inputs"],
            )
            return prediction, result
        except (ValueError, PermissionError, KeyError, RuntimeError):
            return PMPrediction(
                **base,
                answer_text="",
                status="error",
                error="PREDICTION_DEPENDENCY_OR_SCOPE_FAILED",
                model_calls=0,
                image_inputs=0,
            ), None

    def predict_file(self, request_path: Path, out: Path):
        # Parse and deduplicate the entire schedule before any calls or writes.
        requests = [
            PMRequest.model_validate_json(line)
            for line in request_path.read_text().splitlines()
            if line.strip()
        ]
        ids = [r.question_id for r in requests]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("empty/duplicate prediction schedule")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x") as output:
            for request in requests:
                prediction, result = self.predict(request)
                run_path = out.parent / (out.name + ".runs")
                run_path.mkdir(exist_ok=True)
                with (run_path / (prediction.run_id + ".json")).open("x") as run_file:
                    json.dump(
                        result or {"status": "error", "error": prediction.error},
                        run_file,
                        ensure_ascii=False,
                    )
                output.write(prediction.model_dump_json() + "\n")
                output.flush()
        return {
            "count": len(requests),
            "model_mode": self.rag.model_mode,
            "official_benchmark": False,
        }
