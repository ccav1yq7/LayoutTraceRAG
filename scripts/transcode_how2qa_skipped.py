#!/usr/bin/env python3
"""One-time H.264 transcode for videos skipped by the visual benchmark."""
from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/how2qa"
REPORT = ROOT / "results/how2qa_visual_local.json"
MANIFEST = DATA / "val_local_manifest.csv"
OUT = DATA / "videos_h264"
FAILED = DATA / "transcode_failed_ids.txt"


def main() -> None:
    skipped = set(json.loads(REPORT.read_text(encoding="utf-8"))["videos_skipped"])
    source: dict[str, Path] = {}
    with MANIFEST.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["video_id"] in skipped and row["available"] == "1":
                source[row["video_id"]] = ROOT / row["local_video"]
    OUT.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    failures: list[str] = []
    for index, video_id in enumerate(sorted(source), 1):
        target = OUT / f"{video_id}.mp4"
        if target.is_file() and target.stat().st_size > 0:
            continue
        command = [ffmpeg, "-v", "error", "-y", "-i", str(source[video_id]), "-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an", "-movflags", "+faststart", str(target)]
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            failures.append(video_id)
        if index % 20 == 0:
            print(f"transcoded={index - len(failures)}/{len(source)} failed={len(failures)}", flush=True)
    FAILED.write_text("\n".join(failures) + ("\n" if failures else ""), encoding="utf-8")
    print(f"done: requested={len(source)} failed={len(failures)}", flush=True)


if __name__ == "__main__":
    main()
