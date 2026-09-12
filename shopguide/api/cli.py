"""Local M5 server; deployment/auth infrastructure remains a separate milestone."""

from pathlib import Path


def register(sub):
    parser = sub.add_parser("serve", help="run the local API and built React UI")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--web-dist", type=Path, default=Path("web/dist"))
    parser.add_argument("--port", type=int, default=8484)
    parser.add_argument(
        "--log-level", choices=["debug", "info", "warning", "error"], default="info"
    )
    parser.add_argument("--principal", default="user_demo")
    parser.add_argument("--snapshot")
    parser.add_argument("--seed-demo", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--fake", action="store_true")
    parser.add_argument("--test-retrieval", action="store_true")
    parser.add_argument("--private-config", type=Path)
    for name in (
        "embedding-model",
        "embedding-revision",
        "reranker-model",
        "reranker-revision",
    ):
        parser.add_argument("--" + name)


def handle(args, parser):
    import uvicorn

    from ..agent.cli import components
    from ..retrieval.store import ScopedIndex
    from ..storage.repository import Repository
    from ..storage.snapshots import Snapshots
    from .app import create_app
    from .demo import WebDemoGateway, seed_demo

    if not args.demo:
        parser.error(
            "this local CLI requires --demo; non-demo API authentication must be configured by the host application"
        )
    if not 1024 <= args.port <= 65535:
        parser.error("port out of range")
    embedder, reranker, gateway = components(args, parser)
    repository = Repository(args.root / "metadata.db")
    snapshot = (
        seed_demo(repository, args.root)
        if args.seed_demo
        else args.snapshot or Snapshots(repository).active("demo")
    )
    if not snapshot:
        parser.error("no active snapshot; ingest data or use --seed-demo")

    def index(identifier):
        return ScopedIndex(
            repository,
            args.root / "assets",
            args.root / "indexes",
            identifier,
            embedder,
        )

    def factory():
        import copy

        return WebDemoGateway() if args.fake else copy.copy(gateway)

    app = create_app(
        repository,
        args.root,
        index,
        reranker,
        factory,
        demo=True,
        principal=args.principal,
        snapshot=snapshot,
        web_dist=args.web_dist.resolve(),
    )
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            proxy_headers=False,
            log_level=args.log_level,
        )
    finally:
        repository.close()
