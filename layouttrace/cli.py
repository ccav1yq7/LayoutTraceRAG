"""Command-line interface: index a video, then ask questions against it."""
from __future__ import annotations

import argparse

from .config import Config
from .graph import answer_question, build_graph
from .index import LanceStore, build_retriever
from .llm import get_engine


def _index(args: argparse.Namespace) -> None:
    from .ingest import ingest_video

    config = Config()
    nodes = ingest_video(args.video, config, with_frames=not args.no_frames)
    LanceStore(config).add(nodes)
    print(f"indexed {len(nodes)} evidence nodes from {args.video} -> {config.db_path}")


def _ask(args: argparse.Namespace) -> None:
    config = Config()
    store = LanceStore(config)
    graph = build_graph(build_retriever(store, config), get_engine(config), config)
    ans = answer_question(graph, args.question)
    print(ans.render())


def _eval(args: argparse.Namespace) -> None:
    from .eval import evaluate, load_examples

    config = Config()
    store = LanceStore(config)
    graph = build_graph(build_retriever(store, config), get_engine(config), config)
    report = evaluate(graph, load_examples(args.examples), iou_threshold=args.iou)
    report.save(args.out)
    print(report.summary())
    print(f"report -> {args.out}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="layouttrace", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="ingest a video into the evidence store")
    p_index.add_argument("video", help="path to the video file")
    p_index.add_argument("--no-frames", action="store_true", help="skip keyframe OCR")
    p_index.set_defaults(func=_index)

    p_ask = sub.add_parser("ask", help="ask a question against the indexed videos")
    p_ask.add_argument("question", help="the question to answer")
    p_ask.set_defaults(func=_ask)

    p_eval = sub.add_parser("eval", help="run the evaluation harness over a labelled set")
    p_eval.add_argument("examples", help="JSON file of labelled examples")
    p_eval.add_argument("--out", default="report.json", help="where to write the report")
    p_eval.add_argument("--iou", type=float, default=0.1, help="localization IoU threshold")
    p_eval.set_defaults(func=_eval)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
