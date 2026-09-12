"""Whitelisted source corpus. Official IDs/questions/answers remain evaluation-only."""

import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image

from ..schemas import BBox
from .contracts import CorpusPage, CorpusRegion


def opaque(prefix: str, *parts: str) -> str:
    return (
        prefix
        + "_"
        + hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()[
            :32
        ]
    )


def prepare_pm209(archive_path: Path, destination: Path):
    if destination.exists():
        raise FileExistsError("prepared destination exists; use a new generation")
    with archive_path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".pm209-staging-", dir=destination.parent))
    corpus = staging / "corpus"
    private = staging / "eval_private"
    (corpus / "images").mkdir(parents=True)
    private.mkdir(mode=0o700)
    counts = {"pages": 0, "regions": 0, "questions": 0}
    split_counts = {}
    try:
        with (
            zipfile.ZipFile(archive_path) as archive,
            (corpus / "pages.jsonl").open("w") as pages,
        ):
            names = set(archive.namelist())
            if len(names) != len(archive.infolist()):
                raise ValueError("duplicate ZIP names")
            if any(
                PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts
                for n in names
            ):
                raise ValueError("unsafe archive path")
            splits = sorted(
                n
                for n in names
                if n.endswith(".jsonl")
                and PurePosixPath(n).stem in ("train", "val", "test")
            )
            if len(splits) != 3:
                raise ValueError("expected three official splits")
            seen_pages = set()
            seen_regions = set()
            for source_name in splits:
                split = PurePosixPath(source_name).stem
                source_root = PurePosixPath(source_name).parent.parent
                page_numbers: dict[str, int] = {}
                qid = 0
                with (
                    archive.open(source_name) as rows,
                    (private / f"{split}.jsonl").open("w") as gold,
                ):
                    for dataid, line in enumerate(rows):
                        row = json.loads(line)
                        filename = PurePosixPath(row["image_filename"])
                        if filename.is_absolute() or ".." in filename.parts:
                            raise ValueError("unsafe image path")
                        raw = archive.read(str(source_root / filename))
                        if len(raw) > 20 * 1024 * 1024:
                            raise ValueError("image byte limit")
                        with Image.open(io.BytesIO(raw)) as image:
                            w, h = image.size
                            if w * h > 25_000_000:
                                raise ValueError("image pixel limit")
                            fmt = image.format
                            image.verify()
                        ext = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(
                            fmt or ""
                        )
                        if ext is None:
                            raise ValueError("unsupported image format")
                        image_hash = hashlib.sha256(raw).hexdigest()
                        image_name = image_hash + "." + ext
                        (corpus / "images" / image_name).write_bytes(raw)
                        manual = opaque("manual", digest, str(filename.parent))
                        page = opaque("page", digest, str(filename))
                        if page in seen_pages:
                            raise ValueError("duplicate source page")
                        seen_pages.add(page)
                        index = page_numbers.get(manual, 0)
                        page_numbers[manual] = index + 1
                        regions = []
                        mapping = []
                        for region in row["bounding_boxes"]:
                            rid = opaque("region", page, str(region["id"]))
                            if rid in seen_regions:
                                raise ValueError("duplicate region identity")
                            seen_regions.add(rid)
                            shape = region["shape"]
                            x, y, rw, rh = (
                                float(shape[k]) for k in ("x", "y", "width", "height")
                            )
                            text = " ".join(
                                str(o["word"]) for o in region.get("ocr_info", [])
                            )
                            regions.append(
                                CorpusRegion(
                                    region_id=rid,
                                    kind=region["structure"],
                                    text=text,
                                    original_xywh=(x, y, rw, rh),
                                    bbox=BBox(
                                        x0=x / w,
                                        y0=y / h,
                                        x1=(x + rw) / w,
                                        y1=(y + rh) / h,
                                    ),
                                )
                            )
                            mapping.append(
                                {"official_region_id": region["id"], "region_id": rid}
                            )
                        cp = CorpusPage(
                            page_id=page,
                            manual_id=manual,
                            index_0based=index,
                            image_sha256=image_hash,
                            image_name=image_name,
                            width=w,
                            height=h,
                            regions=tuple(regions),
                        )
                        pages.write(cp.model_dump_json() + "\n")
                        for qa in row["qa_data"]:
                            # No reordering: upstream qaid is sequential within each split.
                            gold.write(
                                json.dumps(
                                    {
                                        "question_index": qid,
                                        "data_index": dataid,
                                        "official_page_id": row["id"],
                                        "page_id": page,
                                        "manual_id": manual,
                                        "regions": mapping,
                                        "qa": qa,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                            qid += 1
                        counts["pages"] += 1
                        counts["regions"] += len(regions)
                split_counts[split] = {
                    "questions": qid,
                    "manuals": len(page_numbers),
                    "pages": sum(page_numbers.values()),
                }
                counts["questions"] += qid
        with (corpus / "pages.jsonl").open("rb") as f:
            pages_hash = hashlib.file_digest(f, "sha256").hexdigest()
        manifest = {
            "schema_version": 1,
            "role": "source_corpus",
            "dataset": "pm209",
            "source_archive_sha256": digest,
            "pages_sha256": pages_hash,
            "page_count": counts["pages"],
            "region_count": counts["regions"],
        }
        (corpus / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (private / "manifest.json").write_text(
            json.dumps(
                {
                    "role": "evaluation_private",
                    "archive_sha256": digest,
                    "splits": split_counts,
                    "question_count": counts["questions"],
                },
                indent=2,
            )
            + "\n"
        )
        os.rename(staging, destination)
        return counts | {"archive_sha256": digest, "splits": split_counts}
    except BaseException:
        shutil.rmtree(staging)
        raise


def read_corpus(root: Path):
    root = root.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    allowed = {
        "schema_version",
        "role",
        "dataset",
        "source_archive_sha256",
        "pages_sha256",
        "page_count",
        "region_count",
    }
    if (
        set(manifest) != allowed
        or manifest["role"] != "source_corpus"
        or manifest["schema_version"] != 1
    ):
        raise ValueError("source-only manifest required; gold cannot be ingested")
    with (root / "pages.jsonl").open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != manifest["pages_sha256"]:
            raise ValueError("corpus hash mismatch")
    page_ids = set()
    region_ids = set()
    with (root / "pages.jsonl").open() as f:
        for line in f:
            page = CorpusPage.model_validate_json(line)
            if page.page_id in page_ids:
                raise ValueError("duplicate corpus page ID")
            page_ids.add(page.page_id)
            for region in page.regions:
                if region.region_id in region_ids:
                    raise ValueError("duplicate corpus region ID")
                region_ids.add(region.region_id)
            image = (root / "images" / page.image_name).resolve()
            if not image.is_relative_to(root / "images"):
                raise ValueError("image path escapes corpus")
            yield page, image

    if (
        len(page_ids) != manifest["page_count"]
        or len(region_ids) != manifest["region_count"]
    ):
        raise ValueError("corpus manifest counts mismatch")
