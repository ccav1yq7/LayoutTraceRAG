"""Local multi-label BERT fine-tuning; grouped splits and validation-only selection."""

import argparse
import hashlib
import json
import random
from pathlib import Path


def read_dataset(path, labels):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    groups, texts, ids = {}, {}, set()
    for row in rows:
        if row["split"] not in {"train", "val", "test"} or not row["text"].strip():
            raise ValueError("INVALID_SPLIT_OR_TEXT")
        if row["id"] in ids or not set(row["labels"]) <= set(labels):
            raise ValueError("INVALID_ID_OR_LABEL")
        ids.add(row["id"])
        for mapping, key in [
            (groups, row["group_id"]),
            (texts, " ".join(row["text"].split())),
        ]:
            if key in mapping and mapping[key] != row["split"]:
                raise ValueError("CROSS_SPLIT_LEAKAGE")
            mapping[key] = row["split"]
    if {r["split"] for r in rows} != {"train", "val", "test"}:
        raise ValueError("THREE_SPLITS_REQUIRED")
    if set().union(*(set(r["labels"]) for r in rows if r["split"] == "train")) != set(
        labels
    ):
        raise ValueError("MISSING_TRAIN_LABEL")
    return rows


def metrics(probabilities, targets, threshold):
    import numpy as np

    pred = probabilities >= threshold
    gold = targets.astype(bool)
    tp = (pred & gold).sum(axis=0)
    fp = (pred & ~gold).sum(axis=0)
    fn = (~pred & gold).sum(axis=0)
    den = 2 * tp + fp + fn
    f1 = np.divide(2 * tp, den, out=np.zeros(len(tp), dtype=float), where=den != 0)
    return {
        "macro_f1": float(f1.mean()),
        "exact_match": float((pred == gold).all(axis=1).mean()),
        "per_label_f1": f1.tolist(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=Path("shopguide/intent/taxonomy.json"),
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--device", default="cuda:1")
    args = parser.parse_args()
    if not 1 <= args.epochs <= 30:
        raise ValueError("INVALID_EPOCH_BUDGET")
    import numpy as np
    import torch
    import transformers
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    labels = [
        d["intent_id"] for d in json.loads(args.taxonomy.read_text())["definitions"]
    ]
    rows = read_dataset(args.dataset, labels)
    args.out.mkdir(parents=True, exist_ok=False)
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    protocol = {
        "base": "google-bert/bert-base-chinese",
        "base_revision": args.base_revision,
        "dataset_sha256": sha(args.dataset),
        "taxonomy_sha256": sha(args.taxonomy),
        "script_sha256": sha(Path(__file__)),
        "labels": labels,
        "seed": 20260910,
        "epochs": args.epochs,
        "lr": 3e-5,
        "batch_size": 12,
        "max_length": 96,
        "loss": "BCEWithLogitsLoss with train-only positive weights",
        "threshold_grid": [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        "selection": "validation macro F1 only; test once after checkpoint selection",
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "intended_use": "development bootstrap; not approved for production",
    }
    (args.out / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    random.seed(protocol["seed"])
    np.random.seed(protocol["seed"])
    torch.manual_seed(protocol["seed"])
    torch.set_num_threads(4)
    tok = AutoTokenizer.from_pretrained(args.base, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base,
        local_files_only=True,
        num_labels=len(labels),
        problem_type="multi_label_classification",
    ).to(args.device)
    model.config.id2label = dict(enumerate(labels))
    model.config.label2id = {label: i for i, label in enumerate(labels)}
    data = {}
    for split in ["train", "val", "test"]:
        subset = [r for r in rows if r["split"] == split]
        encoded = tok(
            [r["text"] for r in subset],
            padding=True,
            truncation=True,
            max_length=96,
            return_tensors="pt",
        )
        targets = torch.tensor(
            [[int(label in r["labels"]) for label in labels] for r in subset],
            dtype=torch.float32,
        )
        data[split] = (encoded, targets)
    train_x, train_y = data["train"]
    counts = train_y.sum(0)
    criterion = torch.nn.BCEWithLogitsLoss(
        pos_weight=((len(train_y) - counts) / counts).to(args.device)
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-5, weight_decay=0.01)

    def evaluate(split):
        x, y = data[split]
        outputs = []
        model.eval()
        with torch.no_grad():
            for start in range(0, len(y), 12):
                batch = {k: v[start : start + 12].to(args.device) for k, v in x.items()}
                outputs.append(model(**batch).logits.sigmoid().cpu().numpy())
        return np.concatenate(outputs), y.numpy()

    best = -1.0
    history = []
    threshold = 0.5
    for epoch in range(args.epochs):
        model.train()
        order = torch.randperm(len(train_y))
        losses = []
        for batch_ids in order.split(12):
            optimizer.zero_grad(set_to_none=True)
            batch = {k: v[batch_ids].to(args.device) for k, v in train_x.items()}
            loss = criterion(model(**batch).logits, train_y[batch_ids].to(args.device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append(loss.item())
        prob, gold = evaluate("val")
        grid = [(metrics(prob, gold, t), t) for t in protocol["threshold_grid"]]
        score, t = max(
            grid, key=lambda pair: (pair[0]["macro_f1"], -abs(pair[1] - 0.5))
        )
        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": sum(losses) / len(losses),
                "val": score,
                "threshold": t,
            }
        )
        if score["macro_f1"] > best:
            best = score["macro_f1"]
            threshold = t
            model.save_pretrained(args.out / "checkpoint", safe_serialization=True)
            tok.save_pretrained(args.out / "checkpoint")
            (args.out / "selection.json").write_text(
                json.dumps({"epoch": epoch + 1, "threshold": t, "val": score}, indent=2)
            )
        (args.out / "history.json").write_text(json.dumps(history, indent=2))
        print(json.dumps(history[-1]), flush=True)
    del model
    torch.cuda.empty_cache()
    model = AutoModelForSequenceClassification.from_pretrained(
        args.out / "checkpoint", local_files_only=True
    ).to(args.device)
    prob, gold = evaluate("test")
    result = metrics(prob, gold, threshold)
    (args.out / "test.json").write_text(
        json.dumps(
            {
                "metrics": result,
                "threshold": threshold,
                "samples": len(gold),
                "predictions": prob.tolist(),
                "labels": labels,
            },
            indent=2,
        )
        + "\n"
    )
    (args.out / "model-card.json").write_text(
        json.dumps(
            {
                "status": "development_only",
                "labels": labels,
                "taxonomy_sha256": sha(args.taxonomy),
                "threshold": threshold,
                "checkpoint_sha256": {
                    p.name: sha(p)
                    for p in (args.out / "checkpoint").iterdir()
                    if p.is_file()
                },
                "warning": "authored small dataset; no real customer holdout; no calibrated probabilities",
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"test": result, "threshold": threshold}), flush=True)


if __name__ == "__main__":
    main()
