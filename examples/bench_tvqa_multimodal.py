"""Multimodal TVQA-Long: text-only vs visual-only vs text+visual (Video-MME's
"w/ subtitle" / "w/o subtitle" protocol, adapted) — plus two zero-cost, offline
strengthen-the-visual-signal ideas:

- **CLIP-friendly query rewrite** — CLIP is trained on image/declarative-caption
  pairs, not questions; stripping question words ("what/who/does/is/...") turns
  "What color shirt is Ross wearing?" into "color shirt Ross wearing", closer to
  its training distribution. No model/network needed.
- **CRAG-style on-demand visual trigger** — instead of always fusing text+visual
  (which lets a weak visual signal dilute a confident text answer), only bring in
  the frames when ``HeuristicEngine.grade`` judges the text evidence insufficient.

Conditions reported: text-only, visual-only (raw query), visual-only (rewritten),
text+visual (always fuse), text+visual (adaptive, rewrite+gated).

    python examples/bench_tvqa_multimodal.py --frames-root ~/tvqa_frames/frames \
        --shows "How I Met You Mother" --max-episodes 3 --out data/tvqa_long/report_mm.json

Frames are expected as ``<frames-root>/<clip>/<NNNNN>.jpg`` at ``--frame-fps`` (TVQA
uses 3 fps); we subsample one frame per ``--frame-every`` seconds.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import time
from pathlib import Path

from PIL import Image

from layouttrace.config import Config
from layouttrace.eval.metrics import mean, midpoint_in, overlaps, percentile, temporal_iou
from layouttrace.eval.tvqa_long import load_tvqa_long
from layouttrace.index import InMemoryStore, VisualSearcher, build_retriever, get_embedder, get_vision_embedder
from layouttrace.llm.heuristic import HeuristicEngine
from layouttrace.retrieval.fusion import reciprocal_rank_fusion
from layouttrace.retrieval.hybrid import HybridRetriever
from layouttrace.types import EvidenceNode

K_LIST = (5, 8, 10, 20, 30)

_QUESTION_STOP = {
    "what", "who", "whom", "whose", "where", "why", "how", "when", "which",
    "is", "are", "was", "were", "do", "does", "did", "the", "a", "an", "of",
    "in", "on", "at", "to", "for", "with", "and", "or", "this", "that", "s",
}


def clip_query(question: str) -> str:
    """Strip question/function words -> a caption-like phrase, closer to what
    CLIP's image-caption training distribution expects than a raw question."""
    toks = [w for w in re.findall(r"[A-Za-z']+", question) if w.lower() not in _QUESTION_STOP]
    return " ".join(toks) or question


class RewritingSearcher:
    """Wraps a Searcher (the ``visual=`` slot HybridRetriever's RRF fuses in) so the
    query is rewritten before it reaches CLIP, while text channels see the raw query."""

    def __init__(self, searcher, query_fn) -> None:
        self._searcher = searcher
        self._query_fn = query_fn

    def search(self, query: str, k: int) -> list[str]:
        return self._searcher.search(self._query_fn(query), k)


class VisualOnlyRetriever:
    """No text signal at all — ranks frames purely by CLIP text-query/image similarity."""

    def __init__(self, visual_searcher: VisualSearcher, lookup: dict[str, EvidenceNode],
                 query_fn=lambda q: q) -> None:
        self._vs = visual_searcher
        self._lookup = lookup
        self._query_fn = query_fn

    def retrieve(self, query: str, k: int) -> list[EvidenceNode]:
        q = self._query_fn(query)
        return [self._lookup[i] for i in self._vs.search(q, k) if i in self._lookup]


def _clip_dir(frames_root: str, clip: str) -> str | None:
    for cand in (os.path.join(frames_root, clip),
                 os.path.join(frames_root, clip.split("_", 1)[0], clip)):
        if os.path.isdir(cand):
            return cand
    return None


def build_frame_nodes(frames_root, episode, fps, every_s):
    """Sampled keyframe nodes on the episode timeline: (EvidenceNode, image_path)."""
    out = []
    step = max(1, int(round(every_s * fps)))
    for clip in episode.clips:
        d = _clip_dir(frames_root, clip)
        if not d:
            continue
        off = episode.clip_offset.get(clip, 0.0)
        frames = sorted(glob.glob(os.path.join(d, "*.jpg")))
        for i in range(0, len(frames), step):
            t = off + i / fps
            node = EvidenceNode(id=f"{episode.episode_id}:frame:{clip}:{i}",
                                video_id=episode.episode_id, modality="frame",
                                text="[frame]", start_s=t, end_s=t + every_s)
            out.append((node, frames[i]))
    return out


class AdaptiveRetriever:
    """CRAG-style gate: use text alone when it's judged sufficient; only pay the
    (diluting) cost of fusing in the weaker visual signal when it isn't.

    Uses ``evaluate_evidence`` — the SAME per-document CRAG check the production
    ``grade`` node runs (sufficient iff >=1 node graded "correct", overlap>=0.5) —
    rather than the much looser ``engine.grade()`` (any keyword overlap at all),
    which never judged text insufficient on this sample and so never triggered.
    """

    def __init__(self, retr_text, retr_mm, engine, top_k: int) -> None:
        self._text = retr_text
        self._mm = retr_mm
        self._engine = engine
        self._top_k = top_k
        self.n_triggered = 0
        self.n_total = 0

    def retrieve(self, query: str, k: int) -> list[EvidenceNode]:
        self.n_total += 1
        text_hits = self._text.retrieve(query, k)
        graded = self._engine.evaluate_evidence(query, text_hits[: self._top_k])
        sufficient = sum(1 for g in graded if g.label == "correct") >= 1
        if sufficient:
            return text_hits
        self.n_triggered += 1
        return self._mm.retrieve(query, k)


def recall_row(queries, retriever, gold, iou):
    lookup, rankings = {}, []
    for q in queries:
        hits = retriever.retrieve(q, 40)
        rankings.append([n.id for n in hits])
        for n in hits:
            lookup[n.id] = n
    cand = [lookup[i] for i, _ in reciprocal_rank_fusion(rankings, k=60) if i in lookup]
    spans = [(n.start_s, n.end_s) for n in cand]
    top = spans[0] if spans else None
    row = {"localized": bool(spans) and (temporal_iou(top, gold) >= iou or midpoint_in(top, gold))}
    for K in K_LIST:
        row[f"recall_{K}"] = any(overlaps(s, gold) for s in spans[:K])
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qa", default="data/tvqa_long/tvqa_val_edited.json")
    ap.add_argument("--subs", default="data/tvqa_long/tvqa_preprocessed_subtitles.json")
    ap.add_argument("--frames-root", required=True)
    ap.add_argument("--shows", default="How I Met You Mother")
    ap.add_argument("--max-episodes", type=int, default=3)
    ap.add_argument("--chunk-seconds", type=float, default=30.0)
    ap.add_argument("--frame-fps", type=float, default=3.0)
    ap.add_argument("--frame-every", type=float, default=6.0)
    ap.add_argument("--iou", type=float, default=0.1)
    ap.add_argument("--out", default="data/tvqa_long/report_mm.json")
    args = ap.parse_args()

    cfg = Config()
    episodes = load_tvqa_long(args.qa, args.subs, max_episodes=args.max_episodes,
                              chunk_seconds=args.chunk_seconds, shows={args.shows})
    print(f"{len(episodes)} episodes, {sum(len(e.examples) for e in episodes)} questions")

    embedder = get_embedder(cfg.embed_model)          # BGE-M3
    vemb = get_vision_embedder("clip-ViT-B-32")       # CLIP (cross-modal)
    engine = HeuristicEngine()                        # offline CRAG gate, no LLM/network
    print("models loaded")

    CONDITIONS = ["text_only", "visual_only", "visual_only_rewritten",
                  "text_plus_visual", "text_plus_visual_adaptive"]
    rows = {c: [] for c in CONDITIONS}
    adaptive_triggered = adaptive_total = 0
    for ei, ep in enumerate(episodes, 1):
        store_sub = InMemoryStore(embedder)
        store_sub.add(ep.nodes)                         # subtitles ONLY — clean text baseline

        frames = build_frame_nodes(args.frames_root, ep, args.frame_fps, args.frame_every)
        vs = VisualSearcher(vemb)
        frame_lookup: dict[str, EvidenceNode] = {}
        if frames:
            imgs = [Image.open(fp).convert("RGB") for _, fp in frames]
            vecs = vemb._m.encode(imgs, batch_size=256, show_progress_bar=False)  # batch on GPU
            for (node, _), v in zip(frames, vecs):
                vs.add(node.id, v.tolist())
                frame_lookup[node.id] = node
        print(f"  [{ei}/{len(episodes)}] {ep.episode_id}: {len(ep.nodes)} subs + {len(frames)} frames")

        retr_text = build_retriever(store_sub, cfg)                          # subtitles only
        retr_visual = VisualOnlyRetriever(vs, frame_lookup)                  # frames only, raw query
        retr_visual_rw = VisualOnlyRetriever(vs, frame_lookup, query_fn=clip_query)  # + rewrite
        combined_lookup = lambda nid: store_sub.lookup(nid) or frame_lookup.get(nid)  # noqa: B023
        retr_mm = HybridRetriever(dense=store_sub.dense, lexical=store_sub.lexical,
                                  node_lookup=combined_lookup, candidate_k=cfg.candidate_k,
                                  rrf_k=cfg.rrf_k, visual=vs, visual_weight=cfg.visual_weight)
        retr_mm_rw = HybridRetriever(dense=store_sub.dense, lexical=store_sub.lexical,
                                     node_lookup=combined_lookup, candidate_k=cfg.candidate_k,
                                     rrf_k=cfg.rrf_k, visual=RewritingSearcher(vs, clip_query),
                                     visual_weight=cfg.visual_weight)
        retr_adaptive = AdaptiveRetriever(retr_text, retr_mm_rw, engine, cfg.top_k)

        for ex in ep.examples:
            gold = (ex.gold_start_s, ex.gold_end_s)
            q = [ex.question]
            rows["text_only"].append(recall_row(q, retr_text, gold, args.iou))
            rows["visual_only"].append(recall_row(q, retr_visual, gold, args.iou))
            rows["visual_only_rewritten"].append(recall_row(q, retr_visual_rw, gold, args.iou))
            rows["text_plus_visual"].append(recall_row(q, retr_mm, gold, args.iou))
            rows["text_plus_visual_adaptive"].append(recall_row(q, retr_adaptive, gold, args.iou))
        adaptive_triggered += retr_adaptive.n_triggered
        adaptive_total += retr_adaptive.n_total

    def agg(rs):
        return {**{f"recall_at_{K}": mean([1.0 if r[f"recall_{K}"] else 0.0 for r in rs]) for K in K_LIST},
                "timecode_localization_acc": mean([1.0 if r["localized"] else 0.0 for r in rs])}

    adaptive_rate = adaptive_triggered / adaptive_total if adaptive_total else 0.0
    report = {"benchmark": "TVQA-Long multimodal (w/ vs w/o subtitle + visual-signal strengthening)",
              "shows": args.shows, "n_questions": len(rows["text_only"]), "n_episodes": len(episodes),
              "chunk_seconds": args.chunk_seconds, "frame_every_s": args.frame_every,
              "adaptive_gate": {"triggered": adaptive_triggered, "total": adaptive_total,
                                "trigger_rate": adaptive_rate},
              **{c: agg(rows[c]) for c in CONDITIONS}}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("\n=== TVQA-Long: visual-signal strengthening ablation ===")
    print(f"  adaptive gate triggered visual fallback on {adaptive_triggered}/{adaptive_total} "
          f"questions ({adaptive_rate:.1%})")
    labels = {"text_only": "text-only        (w/ subtitle)",
             "visual_only": "visual-only      (raw query)  ",
             "visual_only_rewritten": "visual-only      (rewritten)  ",
             "text_plus_visual": "text+visual      (always fuse)",
             "text_plus_visual_adaptive": "text+visual      (adaptive)   "}
    for c in CONDITIONS:
        a = report[c]
        print(f"  {labels[c]}: " + "  ".join(f"R@{K}={a[f'recall_at_{K}']:.3f}" for K in K_LIST)
              + f"  Loc={a['timecode_localization_acc']:.3f}")
    print(f"report -> {args.out}")


if __name__ == "__main__":
    main()
