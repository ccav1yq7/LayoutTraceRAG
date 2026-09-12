"""Reproducible authored data expansion; semantic families never cross splits."""

import hashlib
import itertools
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path


def canonical(text):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text)).casefold()


def build(root):
    families = json.loads((root / "families.json").read_text())
    items = [
        {"family": f"{label}:{i:02}", "label": label, "texts": texts}
        for label, pairs in families.items()
        for i, texts in enumerate(pairs)
    ]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[find(b)] = find(a)

    linked = []
    for i, a in enumerate(items):
        for j in range(i):
            b = items[j]
            score = max(
                SequenceMatcher(None, canonical(x), canonical(y)).ratio()
                for x in a["texts"]
                for y in b["texts"]
            )
            if score >= 0.84:
                union(i, j)
                linked.append({"a": a["family"], "b": b["family"], "similarity": score})
    components = defaultdict(list)
    for i, item in enumerate(items):
        components[find(i)].append(item)
    buckets = defaultdict(list)
    for component in components.values():
        buckets[tuple(sorted({i["label"] for i in component}))].append(component)
    assignment = {}
    rng = random.Random(20260910)
    for bucket in buckets.values():
        rng.shuffle(bucket)
        n = len(bucket)
        held = max(1, round(n * 0.15)) if n >= 3 else 0
        for i, component in enumerate(bucket):
            split = "test" if i < held else "val" if i < 2 * held else "train"
            group = (
                "family_group_"
                + hashlib.sha256(
                    "|".join(sorted(x["family"] for x in component)).encode()
                ).hexdigest()[:16]
            )
            for item in component:
                assignment[item["family"]] = (split, group)
    rows = []
    for item in items:
        split, group = assignment[item["family"]]
        for n, text in enumerate(item["texts"]):
            rows.append(
                {
                    "id": f"v2-{item['family']}-{n}",
                    "group_id": group,
                    "source_family_ids": [item["family"]],
                    "split": split,
                    "text": text,
                    "labels": [] if item["label"] == "none" else [item["label"]],
                    "source": "assistant_authored_development_fixture",
                    "kind": "single"
                    if item["label"] != "none"
                    else "no_current_intent",
                }
            )
    labels = [label for label in families if label != "none"]
    for split in ["train", "val", "test"]:
        pool = {
            label: [
                item
                for item in items
                if item["label"] == label
                and assignment[item["family"]][0] == split
                and not any(
                    re.search(r"不用|不要|不想|不是|不需要|暂|只是|只查|只问|只想", t)
                    for t in item["texts"]
                )
            ]
            for label in labels
        }
        for n, (a, b) in enumerate(itertools.combinations(labels, 2)):
            if not pool[a] or not pool[b]:
                continue
            left = pool[a][n % len(pool[a])]
            right = pool[b][(n + 1) % len(pool[b])]
            parents = [left["family"], right["family"]]
            for v in range(2):
                text = (
                    left["texts"][v].rstrip("。？！") + "；另外，" + right["texts"][v]
                )
                rows.append(
                    {
                        "id": f"v2-multi-{split}-{n}-{v}",
                        "group_id": f"multi-{split}-{n}",
                        "source_family_ids": parents,
                        "split": split,
                        "text": text,
                        "labels": [a, b],
                        "source": "assistant_composed_from_same_split_families",
                        "kind": "multi",
                    }
                )
    seen = {}
    family_splits = {}
    group_splits = {}
    for row in rows:
        key = canonical(row["text"])
        if key in seen:
            raise ValueError("DUPLICATE_NORMALIZED_TEXT")
        seen[key] = row["split"]
        for mapping, keys in [
            (family_splits, row["source_family_ids"]),
            (group_splits, [row["group_id"]]),
        ]:
            for key in keys:
                if key in mapping and mapping[key] != row["split"]:
                    raise ValueError("FAMILY_LEAKAGE")
                mapping[key] = row["split"]
    (root / "dataset.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    )
    old = [
        json.loads(x)
        for x in Path("configs/intent/bootstrap.jsonl").read_text().splitlines()
    ]
    overlaps = [
        row["id"]
        for row in rows
        if canonical(row["text"]) in {canonical(o["text"]) for o in old}
    ]
    stats = {
        "version": "intent-expanded-v2",
        "rows": len(rows),
        "source_families": len(items),
        "near_duplicate_components": len(components),
        "splits": dict(Counter(r["split"] for r in rows)),
        "kinds": dict(Counter(r["kind"] for r in rows)),
        "label_counts": {
            s: dict(
                Counter(label for r in rows if r["split"] == s for label in r["labels"])
            )
            for s in ["train", "val", "test"]
        },
        "near_duplicate_links": linked,
        "normalized_overlap_with_bootstrap": overlaps,
        "dataset_sha256": hashlib.sha256(
            (root / "dataset.jsonl").read_bytes()
        ).hexdigest(),
        "family_sha256": hashlib.sha256(
            (root / "families.json").read_bytes()
        ).hexdigest(),
        "seed": 20260910,
        "near_duplicate_threshold": 0.84,
        "limitations": [
            "All samples authored/composed, not real customer logs",
            "Heuristic similarity grouping does not guarantee semantic independence",
            "Multi-intent compositions are templated and grouped by all source families",
            "No automatic retraining or production activation",
        ],
    }
    cross_split_near = []
    for i, row in enumerate(rows):
        for other in rows[:i]:
            if row["split"] == other["split"]:
                continue
            score = SequenceMatcher(
                None, canonical(row["text"]), canonical(other["text"])
            ).ratio()
            if score >= 0.84:
                cross_split_near.append(
                    {"a": row["id"], "b": other["id"], "similarity": score}
                )
    stats["cross_split_near_duplicate_audit"] = cross_split_near
    stats["builder_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (root / "data-card.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n"
    )
    return stats


if __name__ == "__main__":
    stats = build(Path("configs/intent/expanded-v2"))
    print(
        json.dumps(
            {
                k: stats[k]
                for k in ["rows", "source_families", "splits", "kinds", "label_counts"]
            },
            ensure_ascii=False,
        )
    )
