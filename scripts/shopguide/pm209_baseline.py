"""M0-M3 frozen validation preparation and real BGE retrieval, no paid LLM calls.

Only prepare reads eval_private. Retrieval sees the whitelist schedule and corpus.
"""

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

from shopguide.benchmarks.requests import question_id
from shopguide.ingest.pipeline import import_corpus
from shopguide.qa.context import source_page
from shopguide.qa.contracts import PMRequest
from shopguide.retrieval.models import BGEEmbedder, BGEReranker
from shopguide.retrieval.store import ScopedIndex
from shopguide.schemas import ProductVariant
from shopguide.storage.repository import Repository


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open("x") as f:
        json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)


def prepare(args):
    args.out.mkdir(parents=True, exist_ok=False)
    private = args.prepared / "eval_private"
    manifest = json.loads((private / "manifest.json").read_text())
    # Predefined smoke selection: first five manuals in official val order,
    # first ten questions per manual, without examining answer content.
    selected = {}
    for line in (private / "val.jsonl").read_text().splitlines():
        row = json.loads(line)
        manual = row["manual_id"]
        if manual not in selected and len(selected) < 5:
            selected[manual] = []
        if manual in selected and len(selected[manual]) < 10:
            selected[manual].append(row)
    if len(selected) != 5 or any(len(v) != 10 for v in selected.values()):
        raise ValueError("predeclared 5x10 smoke coverage unavailable")
    files = {}
    for profile in ("pm209-given-page", "pm209-retrieved-top1", "pm209-multipage"):
        path = args.out / (profile + ".jsonl")
        with path.open("x") as f:
            for rows in selected.values():
                for row in rows:
                    request = PMRequest(
                        question_id=question_id(
                            manifest["archive_sha256"], "val", row["question_index"]
                        ),
                        manual_id=row["manual_id"],
                        question=row["qa"]["question"]["text"],
                        split="val",
                        protocol=profile,
                        given_page_id=row["page_id"]
                        if profile == "pm209-given-page"
                        else None,
                    )
                    f.write(request.model_dump_json() + "\n")
        files[path.name] = sha(path)
    models = json.loads(args.models.read_text())
    config = {
        "run_kind": "real_baseline_validation",
        "split": "val",
        "question_count": 50,
        "manual_ids": list(selected),
        "selection": "official val order, first 5 manuals x first 10 questions; no label-based filtering; previously used val is development, never fresh holdout",
        "requests": files,
        "private_split_sha256": sha(private / "val.jsonl"),
        "corpus_manifest_sha256": sha(args.prepared / "corpus/manifest.json"),
        "models": {
            name: {"revision": record["revision"]} for name, record in models.items()
        },
        "embedding_max_length": 1024,
        "reranker_max_length": 1024,
        "batch_size": 4,
        "candidates_per_channel": 30,
        "snapshot_id": "snapshot_pm209_bge_val50_v1",
        "principal": "user_research",
        "reference_lock_sha256": sha(Path("configs/pm209/reference.lock.json")),
        "model_version_policy": "provider reports model ID only; immutable DeepSeek revision unknown; validation, not immutable model reproduction",
        "llm_budget_proposal": {
            "profiles": 3,
            "baselines": ["B1", "B2"],
            "questions_per_profile": 50,
            "max_requests": 750,
            "max_output_tokens_per_request": 2048,
            "approved": False,
        },
    }
    write(args.out / "schedule.json", config)
    print(json.dumps({"prepared": 50, "manuals": 5, "model_calls": 0}))


def retrieval(args):
    schedule = json.loads((args.schedule / "schedule.json").read_text())
    requests_path = args.schedule / "pm209-retrieved-top1.jsonl"
    if (
        sha(requests_path) != schedule["requests"][requests_path.name]
        or sha(args.corpus / "manifest.json") != schedule["corpus_manifest_sha256"]
    ):
        raise ValueError("FROZEN_INPUT_CHANGED")
    args.out.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    embedder = BGEEmbedder(
        "BAAI/bge-m3", schedule["models"]["BAAI/bge-m3"]["revision"], device=args.device
    )
    reranker = BGEReranker(
        "BAAI/bge-reranker-v2-m3",
        schedule["models"]["BAAI/bge-reranker-v2-m3"]["revision"],
        device=args.device,
    )
    loaded = time.monotonic()
    imported = import_corpus(
        args.corpus,
        args.root,
        schedule["snapshot_id"],
        schedule["principal"],
        embedder,
        manual_ids=schedule["manual_ids"],
    )
    write(args.out / "index.json", imported)
    built = time.monotonic()
    repo = Repository(args.root / "metadata.db")
    try:
        index = ScopedIndex(
            repo,
            args.root / "assets",
            args.root / "indexes",
            schedule["snapshot_id"],
            embedder,
        )
        health = index.health()
        variants = {v.product_id: v.variant_id for v in repo.all(ProductVariant)}
        with (args.out / "retrieval.jsonl").open("x") as output:
            for line in requests_path.read_text().splitlines():
                request = PMRequest.model_validate_json(line)
                scope = repo.scope(
                    schedule["principal"],
                    request.manual_id,
                    variants[request.manual_id],
                    schedule["snapshot_id"],
                )
                before = time.monotonic()
                hits = index.search(
                    request.question,
                    scope,
                    reranker=reranker,
                    candidates=30,
                    k=30,
                    require_assets=False,
                )
                assert all(
                    h["evidence"].scope.authorized_product_ids
                    == scope.authorized_product_ids
                    for h in hits
                )
                pages = list(dict.fromkeys(source_page(h["evidence"]) for h in hits))
                output.write(
                    json.dumps(
                        {
                            "question_id": request.question_id,
                            "manual_id": request.manual_id,
                            "retrieved_page_ids": pages,
                            "latency_ms": (time.monotonic() - before) * 1000,
                            "hit_count": len(hits),
                            "scope_pass": True,
                        }
                    )
                    + "\n"
                )
                output.flush()
        wrong = BGEEmbedder.__new__(BGEEmbedder)
        wrong.identity = embedder.identity.model_copy(update={"revision": "0" * 40})
        rejected = False
        try:
            ScopedIndex(
                repo,
                args.root / "assets",
                args.root / "indexes",
                schedule["snapshot_id"],
                wrong,
            ).health()
        except ValueError as e:
            rejected = str(e) == "INDEX_IDENTITY_MISMATCH"
        assert rejected
        write(
            args.out / "summary.json",
            {
                "kind": "real_retrieval_validation",
                "llm_calls": 0,
                "questions": 50,
                "pages": imported["pages_imported"],
                "health": health,
                "changed_revision_rejected": rejected,
                "load_seconds": loaded - start,
                "build_seconds": built - loaded,
                "query_seconds": time.monotonic() - built,
                "device": args.device,
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
        )
    finally:
        repo.close()
    print("Real BGE retrieval validation completed", flush=True)


def score_retrieval(args):
    from shopguide.benchmarks.retrieval import retrieval_metrics
    from shopguide.ingest.pm209 import read_corpus

    schedule = json.loads((args.schedule / "schedule.json").read_text())
    path = args.prepared / "eval_private/val.jsonl"
    if sha(path) != schedule["private_split_sha256"]:
        raise ValueError("PRIVATE_SPLIT_CHANGED")
    requests = [
        PMRequest.model_validate_json(l)
        for l in (args.schedule / "pm209-retrieved-top1.jsonl").read_text().splitlines()
    ]
    manifest = json.loads((args.prepared / "eval_private/manifest.json").read_text())
    gold = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        gold[question_id(manifest["archive_sha256"], "val", row["question_index"])] = (
            row
        )
    expected = {
        r.question_id: (r.manual_id, gold[r.question_id]["page_id"]) for r in requests
    }
    pages = list(read_corpus(args.prepared / "corpus"))
    counts = {}
    page_manual = {}
    for page, _ in pages:
        counts[page.manual_id] = counts.get(page.manual_id, 0) + 1
        page_manual[page.page_id] = page.manual_id
    rows = [json.loads(l) for l in args.predictions.read_text().splitlines()]
    for row in rows:
        if any(
            page_manual.get(p) != row["manual_id"] for p in row["retrieved_page_ids"]
        ):
            raise ValueError("FOREIGN_RETRIEVAL_PAGE")
    result = retrieval_metrics(rows, expected, counts)
    result.update(
        {
            "run_kind": "real_BGE_retrieval_validation",
            "official_benchmark": False,
            "llm_calls": 0,
            "predictions_sha256": sha(args.predictions),
            "schedule_sha256": sha(args.schedule / "schedule.json"),
        }
    )
    write(args.out, result)
    print(json.dumps(result))


def campaign(args):
    from shopguide.benchmarks.campaign import prepare_campaign, run_campaign
    from shopguide.models.providers import gateway_from_config

    schedule = json.loads((args.schedule / "schedule.json").read_text())
    embedder = BGEEmbedder(
        "BAAI/bge-m3", schedule["models"]["BAAI/bge-m3"]["revision"], device=args.device
    )
    reranker = BGEReranker(
        "BAAI/bge-reranker-v2-m3",
        schedule["models"]["BAAI/bge-reranker-v2-m3"]["revision"],
        device=args.device,
    )
    gateway = gateway_from_config(args.private_config)
    repo = Repository(args.root / "metadata.db")
    try:
        index = ScopedIndex(
            repo,
            args.root / "assets",
            args.root / "indexes",
            schedule["snapshot_id"],
            embedder,
        )
        index.health()
        if args.command == "campaign-prepare":
            result = prepare_campaign(
                args.schedule, args.out, index, reranker, gateway, pilot=not args.val50
            )
        else:
            result = run_campaign(
                args.out,
                index,
                reranker,
                gateway,
                schedule["principal"],
                approved_max_requests=args.approved_max_requests,
                expected_sha256=args.campaign_sha256,
            )
        print(json.dumps(result))
    finally:
        repo.close()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--prepared", type=Path, required=True)
    p.add_argument("--models", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("retrieval")
    for name in ("schedule", "corpus", "root", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--device", default="cuda:0")
    p = sub.add_parser("score-retrieval")
    for name in ("schedule", "prepared", "predictions", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    for command in ("campaign-prepare", "campaign-run"):
        p = sub.add_parser(command)
        for name in ("schedule", "root", "out", "private-config"):
            p.add_argument("--" + name, type=Path, required=True)
        p.add_argument("--device", default="cuda:0")
        if command == "campaign-prepare":
            p.add_argument("--val50", action="store_true")
        else:
            p.add_argument("--approved-max-requests", type=int, required=True)
            p.add_argument("--campaign-sha256", required=True)
    args = parser.parse_args()
    {
        "score-retrieval": score_retrieval,
        "prepare": prepare,
        "retrieval": retrieval,
        "campaign-prepare": campaign,
        "campaign-run": campaign,
    }[args.command](args)


if __name__ == "__main__":
    main()
