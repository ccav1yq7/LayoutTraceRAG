"""Reproducible eval demo on the built-in synthetic corpus (no models/data needed).

    python examples/eval_demo.py --out examples/report_synthetic.json

For real numbers, index videos with `layouttrace index`, annotate a JSON of
examples (see `layouttrace.eval.EvalExample`), then `layouttrace eval examples.json`.
"""
from __future__ import annotations

import argparse

from layouttrace.config import Config
from layouttrace.eval import evaluate, synthetic_corpus
from layouttrace.graph import build_graph
from layouttrace.index import (
    HashEmbedder,
    HashVisionEmbedder,
    InMemoryStore,
    VisualSearcher,
    build_retriever,
)
from layouttrace.llm import HeuristicEngine
from layouttrace.retrieval import IdentityReranker


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="report.json", help="report output path")
    ap.add_argument("--iou", type=float, default=0.1, help="localization IoU threshold")
    args = ap.parse_args()

    config = Config()
    nodes, examples = synthetic_corpus()
    store = InMemoryStore(HashEmbedder())
    store.add(nodes)

    visual = VisualSearcher(HashVisionEmbedder())
    visual.add_image("lecture01:f:1", "hybrid retrieval dense bm25 rrf diagram")

    graph = build_graph(
        build_retriever(store, config, visual=visual),
        HeuristicEngine(), config, reranker=IdentityReranker(),
    )
    report = evaluate(graph, examples, iou_threshold=args.iou)
    report.save(args.out)
    print(report.summary())
    print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
