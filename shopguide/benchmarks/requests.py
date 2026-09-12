"""Evaluation-side schedule export. This module is not imported by the predictor."""

import json
from pathlib import Path
from typing import Literal

from ..ingest.pm209 import opaque
from ..qa.contracts import PMRequest, Profile


def question_id(archive_hash, split, index):
    return opaque("question", archive_hash, split, str(index))


def export_requests(
    private: Path,
    split: Literal["train", "val", "test"],
    profile: Profile,
    out: Path,
    *,
    limit: int | None = None,
):
    if split not in ("train", "val", "test") or (limit is not None and limit < 1):
        raise ValueError("invalid split/limit")
    manifest = json.loads((private / "manifest.json").read_text())
    if manifest.get("role") != "evaluation_private":
        raise ValueError("evaluation-side manifest required")
    requests: list[PMRequest] = []
    with (private / f"{split}.jsonl").open() as f:
        for line in f:
            if limit is not None and len(requests) >= limit:
                break
            row = json.loads(line)
            requests.append(
                PMRequest(
                    question_id=question_id(
                        manifest["archive_sha256"], split, row["question_index"]
                    ),
                    manual_id=row["manual_id"],
                    question=row["qa"]["question"]["text"],
                    split=split,
                    protocol=profile,
                    given_page_id=row["page_id"]
                    if profile == "pm209-given-page"
                    else None,
                )
            )
    if not requests:
        raise ValueError("empty schedule")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x") as f:
        for request in requests:
            f.write(request.model_dump_json() + "\n")
    return {
        "count": len(requests),
        "protocol": profile,
        "given_information": {"manual": True, "page": profile == "pm209-given-page"},
    }
