"""CLI routing keeps evaluation-only imports out of prediction execution."""

import json
from pathlib import Path

COMMANDS = {"ask", "pm209-predict", "pm209-requests", "pm209-score"}
PROFILES = ["pm209-given-page", "pm209-retrieved-top1", "pm209-multipage"]


def register(sub):
    ask = sub.add_parser("ask", help="fixed B1/B2 answer with mandatory verification")
    ask.add_argument("--question", required=True)
    ask.add_argument("--product", required=True)
    ask.add_argument("--variant", required=True)
    ask.add_argument("--profile", choices=PROFILES, default="pm209-multipage")
    ask.add_argument("--given-page")
    predict = sub.add_parser(
        "pm209-predict", help="predict from whitelisted requests; no gold path"
    )
    predict.add_argument("--requests", type=Path, required=True)
    for command in (ask, predict):
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--snapshot", required=True)
        command.add_argument("--principal", required=True)
        command.add_argument("--baseline", choices=["B1", "B2"], default="B2")
        command.add_argument("--fake", action="store_true")
        command.add_argument(
            "--test-retrieval",
            action="store_true",
            help="explicit hash/RRF retrieval with a real configured LLM; engineering only",
        )
        command.add_argument("--embedding-model")
        command.add_argument("--embedding-revision")
        command.add_argument("--reranker-model")
        command.add_argument("--reranker-revision")
        command.add_argument("--private-config", type=Path)
        command.add_argument("--out", type=Path, required=True)
    export = sub.add_parser(
        "pm209-requests", help="evaluation-side question export without answers"
    )
    export.add_argument("--private", type=Path, required=True)
    export.add_argument("--split", choices=["train", "val", "test"], required=True)
    export.add_argument("--profile", choices=PROFILES, required=True)
    export.add_argument("--limit", type=int)
    export.add_argument("--out", type=Path, required=True)
    score = sub.add_parser(
        "pm209-score",
        help="score with an isolated official reference or explicit engineering diagnostics",
    )
    for field in ("requests", "predictions", "private", "corpus", "out"):
        score.add_argument("--" + field, type=Path, required=True)
    score.add_argument("--engineering", action="store_true")
    for name in (
        "evaluation-manifest",
        "reference-python",
        "reference-source",
        "reference-lock",
        "reference-java-home",
    ):
        score.add_argument("--" + name, type=Path)


def handle(args, parser):
    if args.command == "pm209-requests":
        from ..benchmarks.requests import export_requests

        print(
            json.dumps(
                export_requests(
                    args.private, args.split, args.profile, args.out, limit=args.limit
                )
            )
        )
        return
    if args.command == "pm209-score":
        from ..benchmarks.scoring import score_predictions

        if not args.engineering and not all(
            (
                args.evaluation_manifest,
                args.reference_python,
                args.reference_source,
                args.reference_lock,
                args.reference_java_home,
            )
        ):
            parser.error(
                "formal scoring requires evaluation manifest, reference Python/source/lock"
            )
        if args.engineering and any(
            (
                args.evaluation_manifest,
                args.reference_python,
                args.reference_source,
                args.reference_lock,
                args.reference_java_home,
            )
        ):
            parser.error(
                "engineering and formal reference arguments are mutually exclusive"
            )
        report = score_predictions(
            args.requests,
            args.predictions,
            args.private,
            args.corpus,
            engineering=args.engineering,
            evaluation_manifest=args.evaluation_manifest,
            reference_python=args.reference_python,
            reference_source=args.reference_source,
            reference_lock=args.reference_lock,
            reference_java_home=args.reference_java_home,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x") as f:
            json.dump(report, f, indent=2)
        print(
            json.dumps(
                {
                    "sample_count": report["sample_count"],
                    "official_benchmark": False,
                    "text_scoring": report["text_scoring"],
                }
            )
        )
        return
    from ..retrieval.models import (
        BGEEmbedder,
        BGEReranker,
        Embedder,
        HashEmbedder,
        Reranker,
        RRFReranker,
    )
    from ..retrieval.store import ScopedIndex
    from ..storage.repository import Repository
    from .fixed import FixedRAG
    from .gateway import ExtractiveGateway, Gateway

    embedder: Embedder
    reranker: Reranker
    gateway: Gateway
    if args.fake:
        if any(
            (
                args.private_config,
                args.test_retrieval,
                args.embedding_model,
                args.embedding_revision,
                args.reranker_model,
                args.reranker_revision,
            )
        ):
            parser.error("--fake cannot be combined with real model settings")
        embedder = HashEmbedder()
        reranker = RRFReranker()
        gateway = ExtractiveGateway()
    elif args.test_retrieval:
        if not args.private_config or any(
            (
                args.embedding_model,
                args.embedding_revision,
                args.reranker_model,
                args.reranker_revision,
            )
        ):
            parser.error(
                "--test-retrieval requires private config and no real retrieval model settings"
            )
        from ..models.providers import gateway_from_config

        embedder = HashEmbedder()
        reranker = RRFReranker()
        gateway = gateway_from_config(args.private_config)
    else:
        if not all(
            (
                args.private_config,
                args.embedding_model,
                args.embedding_revision,
                args.reranker_model,
                args.reranker_revision,
            )
        ):
            parser.error(
                "choose explicit --fake or supply private config and pinned embedding/reranker settings"
            )
        from ..models.providers import gateway_from_config

        embedder = BGEEmbedder(args.embedding_model, args.embedding_revision)
        reranker = BGEReranker(args.reranker_model, args.reranker_revision)
        gateway = gateway_from_config(args.private_config)
    if args.baseline == "B2" and not getattr(gateway, "supports_images", True):
        parser.error(
            "configured model is text-only; select --baseline B1 or explicitly configure a vision model"
        )
    repo = Repository(args.root / "metadata.db")
    try:
        index = ScopedIndex(
            repo, args.root / "assets", args.root / "indexes", args.snapshot, embedder
        )
        rag = FixedRAG(index, reranker, gateway, baseline=args.baseline)
        if args.command == "pm209-predict":
            from ..benchmarks.pm209 import PM209Adapter

            print(
                json.dumps(
                    PM209Adapter(rag, args.principal).predict_file(
                        args.requests, args.out
                    )
                )
            )
        else:
            scope = repo.scope(
                args.principal, args.product, args.variant, args.snapshot
            )
            result = rag.run(
                args.question, scope, profile=args.profile, given_page=args.given_page
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(
                json.dumps(
                    {
                        "run_id": result["run_id"],
                        "status": result["status"],
                        "model_mode": result["model_mode"],
                        "answer_status": result["answer"]["status"],
                    }
                )
            )
            if result["status"] != "completed":
                parser.exit(2, "Answer withheld; see run artifact.\n")
    finally:
        repo.close()
