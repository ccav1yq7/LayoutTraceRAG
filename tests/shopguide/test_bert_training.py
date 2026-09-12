import json

import pytest

from shopguide.intent.bert import BertIntentClassifier
from scripts.shopguide.train_intent_bert import read_dataset


def test_group_split_leakage_rejected(tmp_path):
    rows = [
        {
            "id": str(i),
            "text": f"text{i}",
            "group_id": "same",
            "split": split,
            "labels": ["a"],
        }
        for i, split in enumerate(["train", "val", "test"])
    ]
    p = tmp_path / "data.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError, match="LEAKAGE"):
        read_dataset(p, ["a"])


def test_development_checkpoint_requires_explicit_opt_in(tmp_path):
    (tmp_path / "model-card.json").write_text(
        json.dumps({"status": "development_only"})
    )
    with pytest.raises(ValueError, match="NOT_APPROVED"):
        BertIntentClassifier(tmp_path, None)


def test_duplicate_text_across_split_rejected(tmp_path):
    rows = [
        {
            "id": str(i),
            "text": "same text",
            "group_id": str(i),
            "split": split,
            "labels": ["a"],
        }
        for i, split in enumerate(["train", "val", "test"])
    ]
    p = tmp_path / "data.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError, match="LEAKAGE"):
        read_dataset(p, ["a"])


def test_disabled_active_profile_does_not_load_weights(tmp_path):
    from shopguide.intent.bert import load_active_classifier

    p = tmp_path / "active.json"
    p.write_text(json.dumps({"enabled": False}))
    assert load_active_classifier(None, p) is None


def test_active_profile_rejects_changed_card_before_loading(tmp_path):
    from shopguide.intent.bert import load_active_classifier

    (tmp_path / "model-card.json").write_text("{}")
    p = tmp_path / "active.json"
    p.write_text(
        json.dumps({"enabled": True, "model_path": ".", "model_card_sha256": "wrong"})
    )
    with pytest.raises(ValueError, match="ACTIVE_MODEL_CHANGED"):
        load_active_classifier(None, p)


def test_explicit_missing_active_profile_fails(tmp_path):
    from shopguide.intent.bert import load_active_classifier

    with pytest.raises(ValueError, match="CONFIG_MISSING"):
        load_active_classifier(None, tmp_path / "missing.json")
