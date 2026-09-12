"""Fetch/verify the exact BGE files in the checked-in lock; no inference requests."""

import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lock", type=Path, default=Path("configs/pm209/bge.lock.json")
    )
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    records = json.loads(args.lock.read_text())
    result = {}
    for model_id, record in records.items():
        root = Path(
            snapshot_download(
                model_id,
                revision=record["revision"],
                allow_patterns=list(record["files"]),
                local_files_only=not args.download,
            )
        )
        for relative, expected in record["files"].items():
            if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
                raise ValueError("BGE_FILE_CHANGED: " + model_id + "/" + relative)
        result[model_id] = {
            "revision": record["revision"],
            "verified_files": len(record["files"]),
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as f:
        json.dump(
            {
                "lock_sha256": hashlib.sha256(args.lock.read_bytes()).hexdigest(),
                "models": result,
                "model_requests": 0,
            },
            f,
            indent=2,
        )
    print("BGE model files verified")


if __name__ == "__main__":
    main()
