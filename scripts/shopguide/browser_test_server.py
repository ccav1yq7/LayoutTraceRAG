"""Isolated, deliberately delayed fake gateway for deterministic browser cancellation tests."""

import argparse
import time
from pathlib import Path

import uvicorn

from shopguide.api.app import create_app
from shopguide.api.demo import WebDemoGateway, seed_demo
from shopguide.retrieval.models import HashEmbedder, RRFReranker
from shopguide.retrieval.store import ScopedIndex
from shopguide.storage.repository import Repository


class BrowserGateway(WebDemoGateway):
    model_id = "engineering/browser-delayed-fixture"

    def complete(self, *args):
        time.sleep(0.2)
        return super().complete(*args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8486)
    args = parser.parse_args()
    repository = Repository(args.root / "metadata.db")
    snapshot = seed_demo(repository, args.root)

    def index(s):
        return ScopedIndex(
            repository, args.root / "assets", args.root / "indexes", s, HashEmbedder()
        )

    app = create_app(
        repository,
        args.root,
        index,
        RRFReranker(),
        BrowserGateway,
        demo=True,
        snapshot=snapshot,
        web_dist=Path("web/dist").resolve(),
    )
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            proxy_headers=False,
            log_level="warning",
        )
    finally:
        repository.close()


if __name__ == "__main__":
    main()
