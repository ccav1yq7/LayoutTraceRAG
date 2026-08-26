"""Visual-only temporal retrieval benchmark on locally downloaded How2QA videos.

Ranks sampled CLIP frames within each source video using the question text, then
checks whether the top-K timestamps fall in the dataset's annotated QA interval.
This measures visual evidence localization, not multiple-choice answer accuracy.

Run from LayoutTraceRAG:
  uv run python examples/bench_how2qa_visual.py --manifest data/how2qa/val_local_manifest.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from sentence_transformers import SentenceTransformer

K_VALUES = (1, 5, 8, 20)
QUESTION_STOP = {"what", "who", "whom", "whose", "where", "why", "how", "when", "which", "is", "are", "was", "were", "do", "does", "did", "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "and", "or", "this", "that"}


def clip_rewrite(question: str) -> str:
    """Convert a question into caption-like content words for CLIP retrieval."""
    words = [word for word in re.findall(r"[A-Za-z']+", question) if word.lower() not in QUESTION_STOP]
    return " ".join(words) or question


def rrf_rank(score_rows: np.ndarray, rrf_k: int = 60) -> np.ndarray:
    """Fuse raw-question, content-word, and image-caption prompt rankings."""
    fused = np.zeros(score_rows.shape[1], dtype=np.float32)
    for scores in score_rows:
        for rank, index in enumerate(np.argsort(-scores), 1):
            fused[index] += 1.0 / (rrf_k + rank)
    return np.argsort(-fused)


def sample_frames(path: Path, every_s: float) -> tuple[list[Image.Image], list[float]]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return [], []
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, round(fps * every_s))
    frames, timestamps = [], []
    for frame_index in range(0, frame_count, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        if not ok:
            continue
        frames.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        timestamps.append(frame_index / fps)
    cap.release()
    return frames, timestamps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="data/how2qa/val_local_manifest.csv")
    parser.add_argument("--frame-every", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit-videos", type=int, default=0, help="0 evaluates all available videos")
    parser.add_argument("--query-mode", choices=("raw", "rrf"), default="raw")
    parser.add_argument("--out", default="results/how2qa_visual_local.json")
    args = parser.parse_args()

    root = Path.cwd()
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    with Path(args.manifest).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["available"] == "1":
                grouped[row["video_id"]].append(row)
    video_ids = sorted(grouped)
    if args.limit_videos:
        video_ids = video_ids[: args.limit_videos]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer("clip-ViT-B-32", device=device)
    rows: list[dict[str, object]] = []
    skipped: list[str] = []
    for index, video_id in enumerate(video_ids, 1):
        records = grouped[video_id]
        video_path = root / records[0]["local_video"]
        frames, timestamps = sample_frames(video_path, args.frame_every)
        if not frames:
            skipped.append(video_id)
            continue
        frame_embeddings = model.encode(frames, batch_size=args.batch_size, show_progress_bar=False,
                                        normalize_embeddings=True, convert_to_numpy=True)
        questions = [record["question"] for record in records]
        if args.query_mode == "raw":
            question_embeddings = model.encode(questions, batch_size=args.batch_size, show_progress_bar=False,
                                               normalize_embeddings=True, convert_to_numpy=True)
            rankings = [np.argsort(-(embedding @ frame_embeddings.T)) for embedding in question_embeddings]
        else:
            query_groups = [[question, clip_rewrite(question), f"a video showing {clip_rewrite(question)}"] for question in questions]
            flat_queries = [query for group in query_groups for query in group]
            text_embeddings = model.encode(flat_queries, batch_size=args.batch_size, show_progress_bar=False,
                                           normalize_embeddings=True, convert_to_numpy=True)
            rankings = [rrf_rank((text_embeddings[i * 3:(i + 1) * 3] @ frame_embeddings.T)) for i in range(len(questions))]
        for record, order in zip(records, rankings):
            start, end = float(record["start_s"]), float(record["end_s"])
            ranked_times = [timestamps[i] for i in order]
            row: dict[str, object] = {"video_id": video_id, "start_s": start, "end_s": end}
            for k in K_VALUES:
                row[f"recall_at_{k}"] = any(start <= t <= end for t in ranked_times[:k])
            row["top1_localized"] = row["recall_at_1"]
            rows.append(row)
        if index % 25 == 0:
            print(f"videos={index}/{len(video_ids)} qa={len(rows)} skipped={len(skipped)}", flush=True)

    report = {
        "benchmark": "How2QA local validation, visual-only CLIP temporal retrieval",
        "metric_note": "Frame timestamp falls within the annotated QA interval; this is evidence localization, not answer accuracy.",
        "frame_every_s": args.frame_every,
        "query_mode": args.query_mode,
        "videos_requested": len(video_ids),
        "videos_processed": len({row["video_id"] for row in rows}),
        "videos_skipped": skipped,
        "n_questions": len(rows),
        **{f"recall_at_{k}": float(np.mean([row[f"recall_at_{k}"] for row in rows])) if rows else 0.0 for k in K_VALUES},
        "top1_localization_acc": float(np.mean([row["top1_localized"] for row in rows])) if rows else 0.0,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
