"""Audit the complete official ZIP in place; never put QA/gold into runtime corpus."""

import argparse
import hashlib
import io
import json
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with args.archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {
        "archive_sha256": digest,
        "archive_bytes": args.archive.stat().st_size,
        "splits": {},
        "missing_images": 0,
        "invalid_images": 0,
        "invalid_boxes": 0,
        "dangling_relevant_ids": 0,
        "duplicate_region_ids_within_page": 0,
        "official_benchmark": False,
        "license_review_status": "pending",
    }
    image_sets = {}
    manual_sets = {}
    with zipfile.ZipFile(args.archive) as archive:
        infos = archive.infolist()
        names = set(archive.namelist())
        if any(
            PurePosixPath(i.filename).is_absolute()
            or ".." in PurePosixPath(i.filename).parts
            for i in infos
        ):
            raise ValueError("unsafe archive member")
        bad = archive.testzip()
        if bad is not None:
            raise ValueError("ZIP CRC failure")
        report["zip_crc"] = "passed"
        report["members"] = len(infos)
        report["uncompressed_bytes"] = sum(i.file_size for i in infos)
        report["pdf_files"] = sum(n.lower().endswith(".pdf") for n in names)
        splits = [
            n
            for n in names
            if n.endswith(".jsonl")
            and PurePosixPath(n).stem in ("train", "val", "test")
        ]
        if len(splits) != 3:
            raise ValueError("expected train/val/test files")
        for name in sorted(splits):
            split = PurePosixPath(name).stem
            root = PurePosixPath(name).parent.parent
            rows = [
                json.loads(line)
                for line in archive.read(name).decode().splitlines()
                if line.strip()
            ]
            counts = Counter()
            images = set()
            manuals = set()
            regions = Counter()
            for row in rows:
                counts["pages"] += 1
                image = row["image_filename"]
                images.add(image)
                # This follows the upstream page contrast loader's path identity.
                manuals.add(str(PurePosixPath(image).parent))
                full = str(root / image)
                if full not in names:
                    report["missing_images"] += 1
                    continue
                try:
                    with Image.open(io.BytesIO(archive.read(full))) as im:
                        w, h = im.size
                        im.verify()
                except (OSError, ValueError):
                    report["invalid_images"] += 1
                    continue
                boxes = row["bounding_boxes"]
                ids = {b["id"] for b in boxes}
                report["duplicate_region_ids_within_page"] += len(boxes) - len(ids)
                counts["regions"] += len(boxes)
                for box in boxes:
                    regions[box["structure"]] += 1
                    shape = box["shape"]
                    x, y, bw, bh = (shape[k] for k in ("x", "y", "width", "height"))
                    if not (0 <= x < x + bw <= w and 0 <= y < y + bh <= h):
                        report["invalid_boxes"] += 1
                for qa in row["qa_data"]:
                    counts["questions"] += 1
                    if not isinstance(qa["question"]["text"], str) or not isinstance(
                        qa["answer"]["text"], str
                    ):
                        raise TypeError("invalid QA text")
                    report["dangling_relevant_ids"] += len(
                        set(qa["answer"]["relevant"]) - ids
                    )
            counts["manuals"] = len(manuals)
            counts["unique_images"] = len(images)
            report["splits"][split] = dict(counts) | {
                "region_types": dict(regions),
                "split_sha256": hashlib.sha256(archive.read(name)).hexdigest(),
            }
            image_sets[split] = images
            manual_sets[split] = manuals
        report["overlap"] = {
            f"{a}:{b}": {
                "images": len(image_sets[a] & image_sets[b]),
                "manuals": len(manual_sets[a] & manual_sets[b]),
            }
            for a, b in [("train", "val"), ("train", "test"), ("val", "test")]
        }
    errors = sum(
        report[k]
        for k in (
            "missing_images",
            "invalid_images",
            "invalid_boxes",
            "dangling_relevant_ids",
            "duplicate_region_ids_within_page",
        )
    )
    report["audit_status"] = "passed" if errors == 0 else "needs_review"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "audit_status": report["audit_status"],
                "splits": report["splits"],
                "issues": errors,
            }
        )
    )

    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
