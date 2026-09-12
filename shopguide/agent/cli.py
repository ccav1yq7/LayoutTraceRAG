"""Local development commands; a future API must derive principal from authentication."""

import json
from pathlib import Path

COMMANDS = {
    "session-create",
    "session-show",
    "session-select",
    "agent-message",
    "agent-resume",
    "agent-status",
    "agent-cancel",
    "agent-confirm",
}


def register(sub):
    parsers = {
        name: sub.add_parser(name, help="Agent session/run operation")
        for name in sorted(COMMANDS)
    }
    for parser in parsers.values():
        parser.add_argument("--root", type=Path, required=True)
        parser.add_argument("--principal", required=True)
    parsers["session-create"].add_argument(
        "--domain", choices=["demo", "pm209", "ecom"], default="demo"
    )
    parsers["session-create"].add_argument("--snapshot", required=True)
    for name in ("session-show", "session-select", "agent-message"):
        parsers[name].add_argument("--session", required=True)
    parsers["session-select"].add_argument("--product", required=True)
    parsers["session-select"].add_argument("--variant", required=True)
    for name in ("session-select", "agent-message", "agent-confirm"):
        parsers[name].add_argument("--expected-revision", type=int, required=True)
    parsers["agent-message"].add_argument("--message-id", required=True)
    parsers["agent-message"].add_argument("--text", required=True)
    parsers["agent-message"].add_argument("--submit-only", action="store_true")
    for name in ("agent-resume", "agent-status", "agent-cancel"):
        parsers[name].add_argument("--run", required=True)
    parsers["agent-confirm"].add_argument("--confirmation", required=True)
    parsers["agent-confirm"].add_argument("--arguments-hash", required=True)
    for name in ("agent-message", "agent-resume", "agent-confirm"):
        parser = parsers[name]
        parser.add_argument("--fake", action="store_true")
        parser.add_argument("--test-retrieval", action="store_true")
        parser.add_argument("--private-config", type=Path)
        parser.add_argument("--embedding-model")
        parser.add_argument("--embedding-revision")
        parser.add_argument("--reranker-model")
        parser.add_argument("--reranker-revision")


def components(args, parser):
    from ..retrieval.models import BGEEmbedder, BGEReranker, HashEmbedder, RRFReranker
    from .fixtures import AgentFixtureGateway

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
            parser.error("--fake cannot be mixed with real model settings")
        return HashEmbedder(), RRFReranker(), AgentFixtureGateway()
    if not args.private_config:
        parser.error(
            "supply --fake or --private-config with explicit retrieval settings"
        )
    from ..models.providers import gateway_from_config

    if args.test_retrieval:
        if any(
            (
                args.embedding_model,
                args.embedding_revision,
                args.reranker_model,
                args.reranker_revision,
            )
        ):
            parser.error("test retrieval cannot be mixed with real retrieval settings")
        return HashEmbedder(), RRFReranker(), gateway_from_config(args.private_config)
    if not all(
        (
            args.embedding_model,
            args.embedding_revision,
            args.reranker_model,
            args.reranker_revision,
        )
    ):
        parser.error("pinned embedding and reranker settings required")
    return (
        BGEEmbedder(args.embedding_model, args.embedding_revision),
        BGEReranker(args.reranker_model, args.reranker_revision),
        gateway_from_config(args.private_config),
    )


def public_run(record):
    return {
        "run_id": record["id"],
        "session_id": record["session"],
        "revision": record["revision"],
        "status": record["status"],
        "result": record["result"],
    }


def handle(args, parser):
    from ..retrieval.store import ScopedIndex
    from ..sessions.store import Sessions
    from ..storage.repository import Repository
    from .ledger import SimulatedService
    from .runtime import AgentRunner

    repository = Repository(args.root / "metadata.db")
    sessions = Sessions(repository)
    try:
        if args.command == "session-create":
            result = sessions.create(args.principal, args.domain, args.snapshot)
        elif args.command == "session-show":
            result = sessions.get(args.session, args.principal)
        elif args.command == "session-select":
            result = sessions.select(
                args.session,
                args.principal,
                args.product,
                args.variant,
                args.expected_revision,
            )
        elif args.command == "agent-status":
            record = sessions.run(args.run, args.principal)
            result = public_run(record)
            result["events"] = sessions.events(args.run, args.principal)
        elif args.command == "agent-cancel":
            result = public_run(sessions.cancel(args.run, args.principal))
        else:
            embedder, reranker, gateway = components(args, parser)

            def factory(snapshot):
                return ScopedIndex(
                    repository,
                    args.root / "assets",
                    args.root / "indexes",
                    snapshot,
                    embedder,
                )

            runner = AgentRunner(repository, args.root, factory, reranker, gateway)
            if args.command == "agent-message":
                record = sessions.submit(
                    args.session,
                    args.principal,
                    args.message_id,
                    args.text,
                    args.expected_revision,
                )
                if not args.submit_only:
                    record = runner.execute(record["id"], args.principal)
            elif args.command == "agent-confirm":
                run_id = SimulatedService(sessions).approve(
                    args.confirmation,
                    args.principal,
                    args.arguments_hash,
                    args.expected_revision,
                )
                record = runner.execute(run_id, args.principal)
            else:
                record = runner.execute(args.run, args.principal)
            result = public_run(record)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        repository.close()
