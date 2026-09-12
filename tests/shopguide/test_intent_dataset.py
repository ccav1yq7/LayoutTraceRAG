import hashlib
import json
from pathlib import Path

from scripts.shopguide.build_intent_expansion import build
from scripts.shopguide.train_intent_bert import read_dataset

ROOT = Path("configs/intent/expanded-v2")


def test_expanded_dataset_training_compatibility_and_ancestry():
    labels = [
        d["intent_id"]
        for d in json.loads(
            Path("shopguide/intent/taxonomy.json").read_text()
        )["definitions"]
    ]
    rows = read_dataset(ROOT / "dataset.jsonl", labels)
    families = {}
    for row in rows:
        for parent in row["source_family_ids"]:
            families.setdefault(parent, set()).add(row["split"])
    assert all(len(splits) == 1 for splits in families.values())
    assert any(len(r["labels"]) > 1 for r in rows)
    assert any(not r["labels"] for r in rows)
    for split in ("train", "val", "test"):
        assert set().union(
            *(set(r["labels"]) for r in rows if r["split"] == split)
        ) == set(labels)
    card = json.loads((ROOT / "data-card.json").read_text())
    assert not card["cross_split_near_duplicate_audit"]
    assert not card["normalized_overlap_with_bootstrap"]
    assert (
        hashlib.sha256((ROOT / "dataset.jsonl").read_bytes()).hexdigest()
        == card["dataset_sha256"]
    )


def test_dataset_build_is_reproducible(tmp_path):
    (tmp_path / "families.json").write_bytes((ROOT / "families.json").read_bytes())
    build(tmp_path)
    assert (tmp_path / "dataset.jsonl").read_bytes() == (
        ROOT / "dataset.jsonl"
    ).read_bytes()
    assert (tmp_path / "data-card.json").read_bytes() == (
        ROOT / "data-card.json"
    ).read_bytes()
