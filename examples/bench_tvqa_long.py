"""Real benchmark on TVQA-Long with real models on CPU.

Builds a per-episode long-video corpus from TVQA-Long subtitles, indexes it with
**BGE-M3** dense embeddings, retrieves with hybrid + **bge-reranker-v2-m3** cross-
encoder reranking through the full LangGraph agent, and scores every question
against its gold ``ts`` span:

  Recall@k  — a retrieved evidence span overlaps the gold moment (right video)
  Localize  — the top retrieved span's IoU clears --iou OR its centre is in gold
  Faithful  — Self-RAG verify judged the answer grounded
  AnswerF1  — token overlap vs the gold option (extractive vs multiple-choice)
  Latency   — per-question wall time (mean / p50 / p95), CPU

    python examples/bench_tvqa_long.py --max-episodes 3 --out data/tvqa_long/report.json
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import time
from pathlib import Path

from layouttrace.config import Config
from layouttrace.eval.metrics import mean, midpoint_in, overlaps, percentile, temporal_iou, token_f1
from layouttrace.eval.tvqa_long import load_tvqa_long
from layouttrace.graph import build_graph
from layouttrace.index import InMemoryStore, build_retriever, get_embedder
from layouttrace.llm import get_engine
from layouttrace.retrieval.rerank import CrossEncoderReranker
from layouttrace.retrieval.hybrid import collect_candidates


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qa", default="data/tvqa_long/tvqa_val_edited.json")
    ap.add_argument("--subs", default="data/tvqa_long/tvqa_preprocessed_subtitles.json")
    ap.add_argument("--max-episodes", type=int, default=3)
    ap.add_argument("--max-questions-per-ep", type=int, default=None)
    ap.add_argument("--iou", type=float, default=0.1)
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--chunk-seconds", type=float, default=30.0,
                    help="group subtitles into ~N-second retrieval segments (0 = per line)")
    ap.add_argument("--chunk-stride", type=float, default=None,
                    help="advance the window every N seconds instead of chunk-seconds "
                         "(< chunk-seconds => overlapping sliding windows)")
    ap.add_argument("--out", default="data/tvqa_long/report_tvqa_long.json")
    args = ap.parse_args()

    cfg = dataclasses.replace(
        Config(),
        use_reranker=not args.no_rerank,
        max_iterations=1,   # single retrieve->rerank->grade pass (bounded CPU)
        max_regen=0,
    )

    print("loading TVQA-Long ...")
    episodes = load_tvqa_long(args.qa, args.subs, max_episodes=args.max_episodes,
                              max_questions_per_ep=args.max_questions_per_ep,
                              chunk_seconds=args.chunk_seconds, chunk_stride=args.chunk_stride)
    nq = sum(len(e.examples) for e in episodes)
    nnodes = sum(len(e.nodes) for e in episodes)
    print(f"  {len(episodes)} episodes, {nq} questions, {nnodes} segments "
          f"(~{args.chunk_seconds:.0f}s each)")

    try:
        import torch
        device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    except Exception:
        device = "CPU"
    print(f"loading models (BGE-M3{'' if args.no_rerank else ' + bge-reranker-v2-m3'}) on {device} ...")
    embedder = get_embedder(cfg.embed_model)              # BAAI/bge-m3
    reranker = None if args.no_rerank else CrossEncoderReranker(cfg.rerank_model)
    engine = get_engine(cfg)
    engine_label = (f"LLM: {cfg.llm_provider}/{cfg.llm_model}"
                    if type(engine).__name__ == "LangChainEngine"
                    else "heuristic (extractive, offline)")
    print(f"  engine: {engine_label}")

    from layouttrace.retrieval.fusion import reciprocal_rank_fusion

    K_LIST = (5, 8, 10, 20, 30)
    rows: list[dict] = []
    for ei, ep in enumerate(episodes, 1):
        store = InMemoryStore(embedder)
        store.add(ep.nodes)
        retriever = build_retriever(store, cfg)
        graph = build_graph(retriever, engine, cfg, reranker=reranker)
        for ex in ep.examples:
            gold = (ex.gold_start_s, ex.gold_end_s)
            # --- retrieval quality: shared candidate-fusion path used by the application —
            #     multi-query (HyDE / paraphrase) expansion, RRF-fused, then reranked —
            #     measured directly (standard Recall@k), NOT the CRAG-pruned graph set ---
            t0 = time.perf_counter()
            queries = engine.expand_query(ex.question) or [ex.question]
            cand = collect_candidates(retriever, queries, cfg.candidate_k, cfg.rrf_k)
            if reranker is not None:
                cand = reranker.rerank(ex.question, cand, max(K_LIST))
            retr_ms = (time.perf_counter() - t0) * 1000.0
            spans = [(n.start_s, n.end_s) for n in cand]
            top = spans[0] if spans else None
            row = {
                "qid": ex.id, "episode": ep.episode_id, "retr_ms": retr_ms,
                "localized": bool(spans) and (temporal_iou(top, gold) >= args.iou or midpoint_in(top, gold)),
            }
            for K in K_LIST:
                row[f"recall_{K}"] = any(overlaps(s, gold) for s in spans[:K])
            # --- full agentic pipeline: faithfulness + answer ---
            final = graph.invoke({"question": ex.question})
            row["grounded"] = bool(final.get("grounded", False))
            row["answer_f1"] = token_f1(final.get("answer", ""), ex.gold_answer or "")
            rows.append(row)
        print(f"  [{ei}/{len(episodes)}] {ep.episode_id}: {len(ep.examples)} Q done")

    lat = [r["retr_ms"] for r in rows]
    report = {
        "benchmark": "TVQA-Long (val)", "compute": device,
        "chunk_seconds": args.chunk_seconds,
        "models": {"embed": cfg.embed_model, "rerank": None if args.no_rerank else cfg.rerank_model,
                   "generate": engine_label},
        "n_questions": len(rows), "n_episodes": len(episodes),
        **{f"recall_at_{K}": mean([1.0 if r[f"recall_{K}"] else 0.0 for r in rows]) for K in K_LIST},
        "timecode_localization_acc": mean([1.0 if r["localized"] else 0.0 for r in rows]),
        "faithfulness": mean([1.0 if r["grounded"] else 0.0 for r in rows]),
        "answer_f1_extractive": mean([r["answer_f1"] for r in rows]),
        "retrieval_latency_ms": {"mean": mean(lat), "p50": percentile(lat, 50), "p95": percentile(lat, 95)},
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n=== TVQA-Long ({device}, {engine_label}, {args.chunk_seconds:.0f}s segments) ===")
    print("  n={}  ".format(report['n_questions'])
          + "  ".join(f"R@{K}={report[f'recall_at_{K}']:.3f}" for K in K_LIST)
          + f"  Localize={report['timecode_localization_acc']:.3f}"
          + f"  retr_p95={report['retrieval_latency_ms']['p95']:.0f}ms")
    print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
