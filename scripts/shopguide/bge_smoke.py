"""Real model scope/reopen/source smoke on self-authored fixtures; no LLM calls."""

import argparse
import io
import json
import time
from pathlib import Path

from PIL import Image

from shopguide.ingest.contracts import CorpusRegion
from shopguide.ingest.manuals import ManualIngestor
from shopguide.retrieval.models import BGEEmbedder, BGEReranker
from shopguide.retrieval.store import ScopedIndex
from shopguide.schemas import BBox, Product, ProductVariant
from shopguide.storage.repository import AssetRepository, Repository
from shopguide.storage.snapshots import Snapshots


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--models", type=Path, required=True)
    p.add_argument("--device", default="cuda:1")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    models = json.loads(a.models.read_text())
    e = BGEEmbedder("BAAI/bge-m3", models["BAAI/bge-m3"]["revision"], device=a.device)
    r = BGEReranker(
        "BAAI/bge-reranker-v2-m3",
        models["BAAI/bge-reranker-v2-m3"]["revision"],
        device=a.device,
    )
    texts = [
        "Press the wireless button to connect to Wi-Fi.",
        "Remove the battery before cleaning the camera.",
    ]
    vectors = e.embed_documents(texts)
    scores = r.scores("How do I connect to Wi-Fi?", texts, [0, 0])
    assert (
        len(vectors[0]) == 1024 and vectors[0] != vectors[1] and scores[0] > scores[1]
    )
    repo = Repository(a.out / "metadata.db")
    try:
        snapshot = "snapshot_real_scope"
        Snapshots(repo).begin(snapshot, "demo", {"self_authored": True})
        buf = io.BytesIO()
        Image.new("RGB", (80, 40), "white").save(buf, format="PNG")
        png = buf.getvalue()
        evidence = []
        for number, user in [(1, "user_alice"), (2, "user_alice"), (3, "user_bob")]:
            product = Product(
                product_id=f"product_{number}",
                domain="demo",
                brand="Synthetic",
                model=f"TEST-{number}",
                category="fixture",
            )
            variant = ProductVariant(
                variant_id=f"variant_{number}", product_id=product.product_id
            )
            region = CorpusRegion(
                region_id=f"region_{number}",
                kind="Text",
                text=texts[0],
                original_xywh=(0.0, 0.0, 80.0, 40.0),
                bbox=BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
            )
            evidence += ManualIngestor(
                repo, a.out / "assets", snapshot, "demo"
            ).page_image(
                png,
                product=product,
                variant=variant,
                principal=user,
                basis="self-authored real-BGE scope fixture",
                regions=(region,),
            )
        index = ScopedIndex(repo, a.out / "assets", a.out / "indexes", snapshot, e)
        audit = index.build(evidence)
        index.publish(expected_active=None)
    finally:
        repo.close()
    repo = Repository(a.out / "metadata.db")
    try:
        index = ScopedIndex(repo, a.out / "assets", a.out / "indexes", snapshot, e)
        assert index.health() == audit
        scope = repo.scope("user_alice", "product_1", "variant_1", snapshot)
        channels = {}
        for channel in ("dense", "fts"):
            hits = index.channel("wireless button", scope, channel=channel, k=10)
            assert hits and all(
                h.scope.principal_id == "user_alice"
                and h.scope.authorized_product_ids == ("product_1",)
                for h in hits
            )
            channels[channel] = len(hits)
        hits = index.search(
            "How do I connect to Wi-Fi?", scope, reranker=r, k=1, candidates=10
        )
        assert hits[0]["model_mode"] == "real"
        assert (
            AssetRepository(repo, a.out / "assets").read(
                hits[0]["evidence"].asset_ids[0], scope
            )
            == png
        )
        wrong = BGEEmbedder.__new__(BGEEmbedder)
        wrong.identity = e.identity.model_copy(update={"revision": "0" * 40})
        try:
            ScopedIndex(
                repo, a.out / "assets", a.out / "indexes", snapshot, wrong
            ).health()
        except ValueError as ex:
            assert str(ex) == "INDEX_IDENTITY_MISMATCH"
        else:
            raise AssertionError("changed model identity accepted")
        result = {
            "kind": "real_BGE_on_synthetic_scope_fixture",
            "official_benchmark": False,
            "embedding": e.identity.model_dump(mode="json"),
            "reranker_revision": r.revision,
            "reranker_scores": scores,
            "nonconstant_vectors": True,
            "dense_and_fts_scope": channels,
            "repository_reopened": True,
            "asset_bytes_verified": True,
            "changed_revision_rejected": True,
            "elapsed_seconds": time.monotonic() - start,
            "device": a.device,
            "llm_calls": 0,
        }
        (a.out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
