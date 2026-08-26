#!/usr/bin/env python3
"""Download publicly available How2QA validation videos by YouTube ID.

Usage: python scripts/download_how2qa_val.py
Resumable: existing media files are skipped; failures are logged for later retry.
"""
from __future__ import annotations

import csv
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data/how2qa/how2QA_val_release.csv"
OUT_DIR = ROOT / "data/how2qa/videos"
LOG_PATH = ROOT / "data/how2qa/download.log"
WORKERS = 3


def download(video_id: str) -> tuple[str, bool]:
    # --download-archive makes retries/resumption explicit; 480p limits storage and bandwidth.
    command = [
        "python", "-m", "yt_dlp",
        "--no-playlist",
        "--download-archive", str(ROOT / "data/how2qa/downloaded.txt"),
        "-f", "bv*[height<=480]+ba/b[height<=480]",
        "--merge-output-format", "mp4",
        "--retries", "2",
        "--socket-timeout", "30",
        "-o", str(OUT_DIR / "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    return video_id, result.returncode == 0


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        video_ids = sorted({row[0] for row in csv.reader(handle) if row})
    print(f"How2QA validation: {len(video_ids)} unique videos; output: {OUT_DIR}", flush=True)
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(download, video_id) for video_id in video_ids]
        for index, future in enumerate(as_completed(futures), 1):
            video_id, ok = future.result()
            if not ok:
                failed.append(video_id)
            if index % 25 == 0:
                print(f"completed={index}/{len(video_ids)} failed={len(failed)}", flush=True)
    LOG_PATH.write_text("\n".join(failed) + ("\n" if failed else ""), encoding="utf-8")
    print(f"finished; failed={len(failed)} (listed in {LOG_PATH})", flush=True)


if __name__ == "__main__":
    main()
