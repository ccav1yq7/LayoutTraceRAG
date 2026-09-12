"""Explicitly budgeted PM209 pilot/val runner. Never imports evaluation gold."""

import json
from pathlib import Path

from ..qa.contracts import PMRequest
from ..qa.fixed import FixedRAG
from .official import sha
from .pm209 import PM209Adapter


class RequestBudget:
    def __init__(self, gateway, maximum, journal):
        if maximum < 1:
            raise ValueError("positive approved request cap required")
        self.gateway = gateway
        self.maximum = maximum
        self.used = 0
        self.journal = journal

    def __getattr__(self, name):
        return getattr(self.gateway, name)

    def complete(self, role, request, images, schema):
        if self.used >= self.maximum:
            raise RuntimeError("CAMPAIGN_REQUEST_BUDGET_EXHAUSTED")
        self.used += 1
        # Durable reservation precedes every potentially billable request, including errors.
        with self.journal.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "request": self.used,
                        "role": role,
                        "image_count": len(images),
                        "status": "reserved",
                    }
                )
                + "\n"
            )
            f.flush()
            import os

            os.fsync(f.fileno())
        return self.gateway.complete(role, request, images, schema)


def prepare_campaign(schedule_dir, out, index, reranker, gateway, *, pilot=True):
    schedule = json.loads((schedule_dir / "schedule.json").read_text())
    if index.snapshot != schedule["snapshot_id"]:
        raise ValueError("SNAPSHOT_CHANGED")
    if (
        gateway.model_mode != "real"
        or index.embedder.identity.model_mode != "real"
        or reranker.model_mode != "real"
    ):
        raise ValueError("REAL_COMPONENTS_REQUIRED")
    out.mkdir(parents=True, exist_ok=False)
    jobs = []
    for profile in ("pm209-given-page", "pm209-retrieved-top1", "pm209-multipage"):
        source = schedule_dir / (profile + ".jsonl")
        if sha(source) != schedule["requests"][source.name]:
            raise ValueError("SCHEDULE_CHANGED")
        rows = [
            PMRequest.model_validate_json(line)
            for line in source.read_text().splitlines()
        ]
        if pilot:
            rows = [rows[0], rows[10]]  # first question from each of two frozen manuals
        for baseline in ("B1", "B2"):
            name = profile + "-" + baseline
            request_path = out / (name + ".requests.jsonl")
            request_path.write_text(
                "".join(row.model_dump_json() + "\n" for row in rows)
            )
            configuration = FixedRAG(
                index, reranker, gateway, baseline=baseline
            ).configuration(profile)
            evaluation = {
                key: schedule[key]
                for key in (
                    "run_kind",
                    "split",
                    "private_split_sha256",
                    "corpus_manifest_sha256",
                    "reference_lock_sha256",
                    "model_version_policy",
                    "snapshot_id",
                )
            }
            evaluation.update(
                {
                    "requests_sha256": sha(request_path),
                    "protocol": profile,
                    "baseline": baseline,
                    "configuration": configuration,
                }
            )
            (out / (name + ".evaluation.json")).write_text(
                json.dumps(evaluation, indent=2) + "\n"
            )
            jobs.append(
                {
                    "name": name,
                    "baseline": baseline,
                    "profile": profile,
                    "questions": len(rows),
                    "max_requests": len(rows) * (2 if baseline == "B1" else 3),
                    "requests_sha256": sha(request_path),
                    "evaluation_sha256": sha(out / (name + ".evaluation.json")),
                }
            )
    root = Path(__file__).parents[1]
    campaign = {
        "stage": "pilot" if pilot else "val50",
        "approved": False,
        "jobs": jobs,
        "max_requests": sum(j["max_requests"] for j in jobs),
        "max_output_tokens_per_request": 2048,
        "code_sha256": {
            str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*.py"))
        },
        "model_id": gateway.model_id,
        "provider": gateway.provider,
        "budget_notes": "No automatic retries. Reserved attempts include transport failures. Monetary tariff for this experimental model is not established; request cap is not a currency cap.",
    }
    path = out / "campaign.json"
    path.write_text(json.dumps(campaign, indent=2) + "\n")
    return {
        "max_requests": campaign["max_requests"],
        "campaign_sha256": sha(path),
        "model_calls": 0,
    }


def run_campaign(
    directory,
    index,
    reranker,
    gateway,
    principal,
    *,
    approved_max_requests,
    expected_sha256,
):
    path = directory / "campaign.json"
    if sha(path) != expected_sha256:
        raise ValueError("CAMPAIGN_CHANGED")
    campaign = json.loads(path.read_text())
    if approved_max_requests != campaign["max_requests"]:
        raise ValueError("APPROVED_CAP_MUST_MATCH_FROZEN_CAMPAIGN")
    root = Path(__file__).parents[1]
    if {
        str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*.py"))
    } != campaign["code_sha256"]:
        raise ValueError("CAMPAIGN_CODE_CHANGED")
    for job in campaign["jobs"]:
        for suffix, key in (
            ("requests.jsonl", "requests_sha256"),
            ("evaluation.json", "evaluation_sha256"),
        ):
            if sha(directory / (job["name"] + "." + suffix)) != job[key]:
                raise ValueError("CAMPAIGN_INPUT_CHANGED")
        expected = json.loads(
            (directory / (job["name"] + ".evaluation.json")).read_text()
        )["configuration"]
        if (
            FixedRAG(index, reranker, gateway, baseline=job["baseline"]).configuration(
                job["profile"]
            )
            != expected
        ):
            raise ValueError("CAMPAIGN_MODEL_CHANGED")
    # A used directory cannot silently reset its budget or overwrite previous predictions.
    with (directory / "started.json").open("x") as f:
        json.dump(
            {
                "approved_requests": approved_max_requests,
                "campaign_sha256": expected_sha256,
            },
            f,
        )
    budget = RequestBudget(
        gateway, approved_max_requests, directory / "request-reservations.jsonl"
    )
    for job in campaign["jobs"]:
        rag = FixedRAG(index, reranker, budget, baseline=job["baseline"])
        PM209Adapter(rag, principal).predict_file(
            directory / (job["name"] + ".requests.jsonl"),
            directory / (job["name"] + ".predictions.jsonl"),
        )
    result = {
        "reserved_requests": budget.used,
        "max_requests": approved_max_requests,
        "completed_schedule": True,
        "official_benchmark": False,
    }
    (directory / "run-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
