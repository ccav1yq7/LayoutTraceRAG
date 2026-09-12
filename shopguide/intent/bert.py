"""Load an explicitly selected fine-tuned multi-label classifier, never a fake head."""

import hashlib
import json
from pathlib import Path


class BertIntentClassifier:
    def __init__(self, root, taxonomy, *, allow_development=False, device="cpu"):
        root = Path(root)
        card = json.loads((root / "model-card.json").read_text())
        if card["status"] != "production_approved" and not allow_development:
            raise ValueError("INTENT_CLASSIFIER_NOT_APPROVED")
        labels = [d.intent_id for d in taxonomy.definitions]
        if card["labels"] != labels:
            raise ValueError("INTENT_CLASSIFIER_LABELS_CHANGED")
        expected_files = set(card["checkpoint_sha256"])
        actual_files = {p.name for p in (root / "checkpoint").iterdir() if p.is_file()}
        if (
            expected_files != actual_files
            or not {"config.json", "model.safetensors"} <= expected_files
        ):
            raise ValueError("INTENT_CLASSIFIER_MANIFEST_INCOMPLETE")
        for name, expected in card["checkpoint_sha256"].items():
            if (
                Path(name).name != name
                or hashlib.sha256((root / "checkpoint" / name).read_bytes()).hexdigest()
                != expected
            ):
                raise ValueError("INTENT_CLASSIFIER_CHECKPOINT_CHANGED")
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            root / "checkpoint", local_files_only=True
        )
        self.model = (
            AutoModelForSequenceClassification.from_pretrained(
                root / "checkpoint", local_files_only=True
            )
            .to(device)
            .eval()
        )
        if [self.model.config.id2label[i] for i in range(len(labels))] != labels:
            raise ValueError("INTENT_CLASSIFIER_HEAD_CHANGED")
        self.labels = labels
        self.device = device
        self.identity = {
            "type": "fine_tuned_bert",
            "status": card["status"],
            "card_sha256": hashlib.sha256(
                (root / "model-card.json").read_bytes()
            ).hexdigest(),
            "probabilities_calibrated": False,
        }

    def scores(self, query):
        import torch

        batch = self.tokenizer(
            query, truncation=True, max_length=96, return_tensors="pt"
        ).to(self.device)
        with torch.no_grad():
            values = self.model(**batch).logits.sigmoid()[0].cpu().tolist()
        return dict(zip(self.labels, values, strict=True))


def load_active_classifier(taxonomy, config_path=None):
    """Load the explicitly installed local profile. Missing weights fail, never fake fallback."""
    import os

    project = Path(__file__).resolve().parents[3]
    config = Path(
        config_path
        or os.environ.get(
            "SHOPGUIDE_INTENT_CLASSIFIER_CONFIG",
            project / "configs/intent/active-classifier.json",
        )
    )
    if not config.exists():
        if (
            config_path is not None
            or "SHOPGUIDE_INTENT_CLASSIFIER_CONFIG" in os.environ
        ):
            raise ValueError("INTENT_CLASSIFIER_CONFIG_MISSING")
        return None
    profile = json.loads(config.read_text())
    if not profile["enabled"]:
        return None
    root = Path(profile["model_path"])
    if not root.is_absolute():
        root = (config.parent / root).resolve()
    actual = hashlib.sha256((root / "model-card.json").read_bytes()).hexdigest()
    if actual != profile["model_card_sha256"]:
        raise ValueError("INTENT_ACTIVE_MODEL_CHANGED")
    return _cached_classifier(
        str(root),
        taxonomy.model_dump_json(),
        actual,
        profile.get("allow_development", False),
        profile.get("device", "cpu"),
    )


from functools import lru_cache


@lru_cache(maxsize=2)
def _cached_classifier(root, taxonomy_json, card_sha, allow_development, device):
    from .service import Taxonomy

    classifier = BertIntentClassifier(
        root,
        Taxonomy.model_validate_json(taxonomy_json),
        allow_development=allow_development,
        device=device,
    )
    if classifier.identity["card_sha256"] != card_sha:
        raise ValueError("INTENT_ACTIVE_MODEL_CHANGED")
    return classifier
