"""ShopGuide ingestion, scoped retrieval and fixed B1/B2 question answering."""

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .schemas import (
    Action,
    Asset,
    DocumentVersion,
    Evidence,
    GuideAnswer,
    Locator,
    Product,
)
from .settings import Settings


def main(argv=None):
    import sys

    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "ops":
        from .ops.cli import main as ops_main

        return ops_main(argv[1:])
    if argv and argv[0] == "ecom":
        from .ecom.cli import main as ecom_main

        return ecom_main(argv[1:])
    parser = argparse.ArgumentParser(prog="shopguide", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ops", help="offline backup, safe restore and release evidence")
    sub.add_parser("ecom", help="pinned native ECom trials and reports")
    schema = sub.add_parser("schema", help="export implemented JSON contracts")
    schema.add_argument("--out", type=Path, required=True)
    doctor = sub.add_parser(
        "doctor", help="validate configuration; does not call a model"
    )
    doctor.add_argument("--config", type=Path, required=True)
    data = sub.add_parser(
        "prepare-pm209", help="separate source corpus and private gold"
    )
    data.add_argument("--archive", type=Path, required=True)
    data.add_argument("--out", type=Path, required=True)
    ingest = sub.add_parser(
        "ingest-corpus", help="build and publish a source-only PM209 generation"
    )
    ingest.add_argument("--corpus", type=Path, required=True)
    ingest.add_argument("--max-pages", type=int)
    pdf = sub.add_parser("ingest-pdf", help="ingest a locally authorized PDF")
    pdf.add_argument("--file", type=Path, required=True)
    pdf.add_argument("--product", required=True)
    pdf.add_argument("--variant", required=True)
    pdf.add_argument("--brand", required=True)
    pdf.add_argument("--model", required=True)
    pdf.add_argument("--basis", required=True)
    search = sub.add_parser(
        "search", help="query an audited snapshot with backend authorization"
    )
    search.add_argument("--query", required=True)
    search.add_argument("--product", required=True)
    search.add_argument("--variant", required=True)
    search.add_argument("--k", type=int, default=8)
    search.add_argument("--reranker-model")
    search.add_argument("--reranker-revision")
    for command in (ingest, pdf, search):
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--snapshot", required=True)
        command.add_argument("--principal", required=True)
        command.add_argument(
            "--fake",
            action="store_true",
            help="explicit engineering-only hash/RRF profile",
        )
        command.add_argument("--embedding-model")
        command.add_argument("--embedding-revision")
    from .qa.cli import COMMANDS, handle, register

    register(sub)
    from .agent.cli import COMMANDS as AGENT_COMMANDS
    from .agent.cli import handle as handle_agent
    from .agent.cli import register as register_agent

    register_agent(sub)
    from .api.cli import handle as handle_api
    from .api.cli import register as register_api

    register_api(sub)
    args = parser.parse_args(argv)
    if args.command == "serve":
        handle_api(args, parser)
        return
    if args.command in AGENT_COMMANDS:
        handle_agent(args, parser)
        return
    if args.command in COMMANDS:
        handle(args, parser)
        return
    if args.command == "prepare-pm209":
        from .ingest.pm209 import prepare_pm209

        print(json.dumps(prepare_pm209(args.archive, args.out)))
        return
    if args.command in ("ingest-corpus", "ingest-pdf", "search"):
        from .retrieval.models import (
            BGEEmbedder,
            BGEReranker,
            Embedder,
            HashEmbedder,
            Reranker,
            RRFReranker,
        )
        from .schemas import ProductVariant

        embedder: Embedder
        reranker: Reranker
        if args.fake:
            if args.embedding_model or args.embedding_revision:
                parser.error(
                    "--fake cannot be combined with a real embedding configuration"
                )
            embedder = HashEmbedder()
        elif args.embedding_model and args.embedding_revision:
            embedder = BGEEmbedder(args.embedding_model, args.embedding_revision)
        else:
            parser.error(
                "explicit --fake or pinned embedding model/revision is required"
            )
        if args.command == "ingest-corpus":
            from .ingest.pipeline import import_corpus

            print(
                json.dumps(
                    import_corpus(
                        args.corpus,
                        args.root,
                        args.snapshot,
                        args.principal,
                        embedder,
                        max_pages=args.max_pages,
                    )
                )
            )
        elif args.command == "ingest-pdf":
            from .ingest.pipeline import import_pdf

            product = Product(
                product_id=args.product,
                domain="demo",
                brand=args.brand,
                model=args.model,
                category="manual",
            )
            variant = ProductVariant(variant_id=args.variant, product_id=args.product)
            print(
                json.dumps(
                    import_pdf(
                        args.file,
                        args.root,
                        args.snapshot,
                        args.principal,
                        product,
                        variant,
                        args.basis,
                        embedder,
                    )
                )
            )
        else:
            from .retrieval.store import ScopedIndex
            from .storage.repository import Repository

            if args.fake:
                reranker = RRFReranker()
            elif args.reranker_model and args.reranker_revision:
                reranker = BGEReranker(args.reranker_model, args.reranker_revision)
            else:
                parser.error("real search requires a pinned reranker model/revision")
            repo = Repository(args.root / "metadata.db")
            try:
                scope = repo.scope(
                    args.principal, args.product, args.variant, args.snapshot
                )
                index = ScopedIndex(
                    repo,
                    args.root / "assets",
                    args.root / "indexes",
                    args.snapshot,
                    embedder,
                )
                results = index.search(args.query, scope, reranker=reranker, k=args.k)
                print(
                    json.dumps(
                        [
                            {**r, "evidence": r["evidence"].model_dump(mode="json")}
                            for r in results
                        ],
                        ensure_ascii=False,
                    )
                )
            finally:
                repo.close()
        return
    if args.command == "schema":
        from .agent.contracts import (
            AgentBudget,
            ImageObservation,
            PlanDecision,
            ServiceArgs,
        )
        from .benchmarks.pm209 import PMPrediction
        from .ingest.contracts import CorpusPage, Relation
        from .qa.contracts import PMRequest, RAGBudget, SemanticVerdict, WriterDraft
        from .retrieval.models import IndexIdentity

        contracts = {
            name: TypeAdapter(cls).json_schema()
            for name, cls in [
                ("Product", Product),
                ("DocumentVersion", DocumentVersion),
                ("Locator", Locator),
                ("Asset", Asset),
                ("Evidence", Evidence),
                ("Action", Action),
                ("Answer", GuideAnswer),
                ("CorpusPage", CorpusPage),
                ("Relation", Relation),
                ("IndexIdentity", IndexIdentity),
                ("RAGBudget", RAGBudget),
                ("WriterDraft", WriterDraft),
                ("SemanticVerdict", SemanticVerdict),
                ("PMRequest", PMRequest),
                ("PMPrediction", PMPrediction),
                ("AgentBudget", AgentBudget),
                ("PlanDecision", PlanDecision),
                ("ImageObservation", ImageObservation),
                ("ServiceArgs", ServiceArgs),
            ]
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(contracts, indent=2) + "\n")
    else:
        try:
            settings = Settings.model_validate_json(args.config.read_text())
        except (ValidationError, OSError):
            parser.exit(2, "BLOCKED: configuration invalid or unreadable\n")
        print(
            json.dumps(
                {
                    "config_valid": True,
                    "model_mode": settings.model_mode,
                    "formal": settings.formal,
                    "model_service_verified": False,
                }
            )
        )


if __name__ == "__main__":
    main()
