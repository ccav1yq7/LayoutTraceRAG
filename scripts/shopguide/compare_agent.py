"""Run the fixed shared-tool control and Agent on one explicitly supplied example."""

import argparse
import copy
import json
from pathlib import Path

from shopguide.agent.cli import components
from shopguide.agent.comparison import compare
from shopguide.retrieval.store import ScopedIndex
from shopguide.storage.repository import Repository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    for name in ("principal", "product", "variant", "snapshot", "question"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--out", type=Path, required=True)
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
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output exists; choose a new report path")
    embedder, reranker, gateway = components(args, parser)
    repository = Repository(args.root / "metadata.db")
    try:

        def index(snapshot):
            return ScopedIndex(
                repository,
                args.root / "assets",
                args.root / "indexes",
                snapshot,
                embedder,
            )

        result = compare(
            repository,
            args.root,
            index,
            reranker,
            lambda: copy.copy(gateway),
            args.principal,
            args.product,
            args.variant,
            args.snapshot,
            args.question,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(
            json.dumps(
                {
                    name: {"status": r["status"], "usage": r["result"]["usage"]}
                    for name, r in result["results"].items()
                }
            )
        )
    finally:
        repository.close()


if __name__ == "__main__":
    main()
