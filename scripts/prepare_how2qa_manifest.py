#!/usr/bin/env python3
"""Create a local How2QA validation manifest for downloaded media."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/how2qa"
VIDEO_DIR = DATA / "videos"
TRANSCODED_DIR = DATA / "videos_h264"
INPUT = DATA / "how2QA_val_release.csv"
OUTPUT = DATA / "val_local_manifest.csv"
MISSING = DATA / "missing_video_ids.txt"
SUMMARY = DATA / "download_summary.json"

# Prefer a single muxed MP4, then a WebM video; m4a-only files are not usable for visual QA.
PREFERENCE = {"mp4": 0, "webm": 1}


def media_for(video_id: str) -> Path | None:
    """Prefer one-time H.264 outputs, then readable original video streams."""
    transcoded = TRANSCODED_DIR / f"{video_id}.mp4"
    if transcoded.is_file():
        return transcoded
    choices = []
    for path in VIDEO_DIR.glob(f"{video_id}.*"): 
        if path.suffix.lstrip(".") not in PREFERENCE:
            continue
        capture = cv2.VideoCapture(str(path))
        has_video = capture.isOpened() and capture.get(cv2.CAP_PROP_FRAME_WIDTH) > 0
        capture.release()
        if has_video:
            choices.append(path)
    return min(choices, key=lambda p: (PREFERENCE[p.suffix.lstrip(".")], len(p.name))) if choices else None


def main() -> None:
    rows = []
    with INPUT.open(newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if not row:
                continue
            video_id, span, *rest = row
            start, end = span[1:-1].split(":")
            rows.append({"video_id": video_id, "start_s": start, "end_s": end, "raw": row})

    ids = sorted({row["video_id"] for row in rows})
    local = {video_id: media_for(video_id) for video_id in ids}
    available = {video_id for video_id, path in local.items() if path}

    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["video_id", "start_s", "end_s", "local_video", "available", "answer_2", "answer_3", "answer_4", "question", "answer_1"])
        for row in rows:
            path = local[row["video_id"]]
            writer.writerow([row["video_id"], row["start_s"], row["end_s"], str(path.relative_to(ROOT)) if path else "", int(path is not None), *row["raw"][2:]])

    MISSING.write_text("\n".join(video_id for video_id in ids if video_id not in available) + "\n", encoding="utf-8")
    summary = {
        "split": "validation",
        "qa_rows": len(rows),
        "unique_video_ids": len(ids),
        "video_ids_with_local_video": len(available),
        "qa_rows_with_local_video": sum(row["video_id"] in available for row in rows),
        "video_ids_without_local_video": len(ids) - len(available),
        "media_directory": str(VIDEO_DIR.relative_to(ROOT)),
        "manifest": str(OUTPUT.relative_to(ROOT)),
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
