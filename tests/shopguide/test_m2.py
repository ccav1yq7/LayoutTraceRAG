import hashlib
import io
import json
import zipfile

import pytest
from PIL import Image

from shopguide.ingest.contracts import CorpusRegion, Relation
from shopguide.ingest.manuals import ManualIngestor
from shopguide.ingest.pm209 import prepare_pm209, read_corpus
from shopguide.retrieval.models import HashEmbedder, RRFReranker
from shopguide.retrieval.store import ScopedIndex
from shopguide.schemas import BBox, Evidence, Product, ProductVariant
from shopguide.storage.repository import AssetRepository, Repository
from shopguide.storage.snapshots import Snapshots


def png():
    out = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(out, format="PNG")
    return out.getvalue()


def region(text, kind="Text", rid="region_text"):
    return CorpusRegion(
        region_id=rid,
        kind=kind,
        text=text,
        original_xywh=(0.0, 0.0, 80.0, 40.0),
        bbox=BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
    )


def add_page(repo, root, snapshot, number, text, principal="user_alice"):
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
    ingestor = ManualIngestor(repo, root / "assets", snapshot, "demo")
    return ingestor.page_image(
        png(),
        product=product,
        variant=variant,
        principal=principal,
        basis="Self-authored fixture",
        regions=(region(text),),
    )


@pytest.fixture
def generation(tmp_path):
    repo = Repository(tmp_path / "metadata.db")
    snapshots = Snapshots(repo)
    snapshots.begin("snapshot_first", "demo", {"fixture": 1})
    evidence = add_page(repo, tmp_path, "snapshot_first", 1, "connect wifi")
    evidence += add_page(
        repo, tmp_path, "snapshot_first", 2, "connect wifi button connect wifi button"
    )
    evidence += add_page(
        repo, tmp_path, "snapshot_first", 3, "connect wifi button", "user_bob"
    )
    index = ScopedIndex(
        repo, tmp_path / "assets", tmp_path / "index", "snapshot_first", HashEmbedder()
    )
    yield repo, snapshots, evidence, index, tmp_path
    repo.close()


def scope_for(repo, snapshot="snapshot_first", product=1, principal="user_alice"):
    return repo.scope(principal, f"product_{product}", f"variant_{product}", snapshot)


def test_i01_i03_i04_real_lance_prefilter_reopen(generation):
    repo, snapshots, evidence, index, root = generation
    scope = scope_for(repo)
    with pytest.raises(PermissionError):
        AssetRepository(repo, root / "assets").read(evidence[0].asset_ids[0], scope)
    manifest = index.build(evidence)
    assert manifest["row_count"] == 6
    index.publish(expected_active=None)
    assert snapshots.active("demo") == "snapshot_first"
    # Prove globally closer evidence exists, then insist scoped top1 still succeeds.
    nearest = (
        index._table()
        .search(index.embedder.embed_query("connect wifi button"))
        .distance_type("cosine")
        .limit(1)
        .to_list()[0]
    )
    assert nearest["product"] != "product_1"
    for channel in ("fts", "dense"):
        hits = index.channel("connect wifi button", scope, channel=channel, k=1)
        assert len(hits) == 1 and hits[0].scope.authorized_product_ids == ("product_1",)
    reopened = ScopedIndex(
        repo, root / "assets", root / "index", "snapshot_first", HashEmbedder()
    )
    assert reopened.health() == manifest
    hits = reopened.search(
        "connect wifi", scope, reranker=RRFReranker(), candidates=3, k=1
    )
    assert hits[0]["model_mode"] == "fake"
    assert hits[0]["retrieval_trace"]
    assert (
        AssetRepository(repo, root / "assets").read(
            hits[0]["evidence"].asset_ids[0], scope
        )
        == png()
    )


def test_i05_active_pointer_pinned_old_and_cas(generation):
    repo, snapshots, evidence, index, root = generation
    index.build(evidence)
    index.publish(expected_active=None)
    snapshots.begin("snapshot_next", "demo", {"fixture": 2})
    newer = add_page(repo, root, "snapshot_next", 1, "updated connect guide")
    replacement = ScopedIndex(
        repo, root / "assets", root / "index", "snapshot_next", HashEmbedder()
    )
    replacement.build(newer)
    with pytest.raises(ValueError, match="pointer"):
        replacement.publish(expected_active=None)
    assert snapshots.active("demo") == "snapshot_first"
    replacement.publish(expected_active="snapshot_first")
    assert snapshots.active("demo") == "snapshot_next"
    assert index.channel("wifi", scope_for(repo), channel="fts")
    assert replacement.channel(
        "updated", scope_for(repo, "snapshot_next"), channel="fts"
    )
    # A rollback is another checked publication, not overwriting the old generation.
    index.publish(expected_active="snapshot_next")
    assert snapshots.active("demo") == "snapshot_first"


@pytest.mark.parametrize("failure", ["count", "nan", "dimension", "zero"])
def test_u07_u08_i06_failed_build_restart(generation, failure):
    _repo, snapshots, evidence, index, _root = generation

    class Broken(HashEmbedder):
        def embed_documents(self, texts):
            if failure == "count":
                return []
            if failure == "nan":
                return [[float("nan")] * 32 for _ in texts]
            if failure == "dimension":
                return [[1.0] for _ in texts]
            return [[0.0] * 32 for _ in texts]

    index.embedder = Broken()
    with pytest.raises(ValueError):
        index.build(evidence)
    assert snapshots.get("snapshot_first")["state"] == "FAILED"
    assert snapshots.active("demo") is None
    snapshots.begin("snapshot_first", "demo", {"fixture": 1})
    index.embedder = HashEmbedder()
    index.build(evidence)
    index.publish(expected_active=None)
    assert index.health()["row_count"] == len(evidence)


def test_i06_partial_import_retry_is_idempotent(generation):
    repo, _snapshots, evidence, index, root = generation
    repeat = add_page(repo, root, "snapshot_first", 1, "connect wifi")
    assert [e.evidence_id for e in repeat] == [e.evidence_id for e in evidence[:2]]
    assert len(repo.all(Evidence)) == 6
    index.build(evidence)


def test_s02_s05_s07_s09_scope_revocation_and_tamper(generation):
    repo, _snapshots, evidence, index, _root = generation
    index.build(evidence)
    index.publish(expected_active=None)
    scope = scope_for(repo)
    for update in (
        {"principal_id": "user_bob"},
        {"domain": "pm209"},
        {"snapshot_id": "snapshot_other"},
        {"authorized_product_ids": ("x' OR 1=1 --",)},
    ):
        with pytest.raises((PermissionError, ValueError)):
            index.channel("wifi", scope.model_copy(update=update), channel="fts")
    repo.revoke(evidence[0].source_locator.doc_version_id)
    with pytest.raises(PermissionError):
        index.channel("wifi", scope, channel="dense")
    with pytest.raises(PermissionError):
        AssetRepository(repo, index.asset_root).read(evidence[0].asset_ids[0], scope)


def test_index_identity_and_corruption(generation):
    repo, _snapshots, evidence, index, root = generation
    index.build(evidence)
    changed = HashEmbedder()
    changed.identity = changed.identity.model_copy(update={"revision": "fake-v2"})
    reopened = ScopedIndex(
        repo, root / "assets", root / "index", "snapshot_first", changed
    )
    with pytest.raises(ValueError, match="IDENTITY"):
        reopened.health()
    index._table().delete("id = '" + evidence[0].evidence_id + "'")
    with pytest.raises(ValueError, match="count"):
        index.health()


def test_missing_asset_prevents_publication(generation):
    repo, snapshots, evidence, index, root = generation
    index.build(evidence)
    assets = AssetRepository(repo, root / "assets")
    assets._path(hashlib.sha256(png()).hexdigest()).unlink()
    with pytest.raises(FileNotFoundError):
        index.publish(expected_active=None)
    assert snapshots.active("demo") is None


def test_layout_relations_keep_heuristic_basis(tmp_path):
    repo = Repository(tmp_path / "db")
    snapshots = Snapshots(repo)
    snapshots.begin("snapshot_rel", "demo", {})
    try:
        ingestor = ManualIngestor(repo, tmp_path / "assets", "snapshot_rel", "demo")
        product = Product(
            product_id="product_one",
            domain="demo",
            brand="Synthetic",
            model="TEST",
            category="fixture",
        )
        ingestor.page_image(
            png(),
            product=product,
            variant=ProductVariant(variant_id="variant_one", product_id="product_one"),
            principal="user_alice",
            basis="fixture",
            regions=(
                region("Power button"),
                region("", kind="illustration", rid="region_picture"),
            ),
        )
        relations = repo.all(Relation)
        assert len(relations) == 3
        near = next(r for r in relations if r.kind == "nearby_text")
        assert near.inferred and "not a verified caption" in near.basis
    finally:
        repo.close()


def tiny_archive(path, unsafe=False):
    with zipfile.ZipFile(path, "w") as z:
        for split in ("train", "val", "test"):
            name = f"images/{split}/manual/page.png"
            row = {
                "id": split,
                "image_filename": ("../escape.png" if unsafe else name),
                "bounding_boxes": [
                    {
                        "id": "official_region",
                        "structure": "Text",
                        "shape": {"x": 0, "y": 0, "width": 80, "height": 40},
                        "ocr_info": [{"word": "SOURCE_ONLY"}],
                    }
                ],
                "qa_data": [
                    {
                        "id": "official_qa",
                        "question": {"text": "PRIVATE_QUESTION_SENTINEL"},
                        "answer": {
                            "text": "PRIVATE_ANSWER_SENTINEL",
                            "relevant": ["official_region"],
                        },
                    }
                ],
            }
            z.writestr(f"PM209/data/{split}.jsonl", json.dumps(row) + "\n")
            z.writestr("PM209/" + name, png())


def test_pm209_whitelist_and_mapping_roundtrip(tmp_path):
    archive = tmp_path / "source.zip"
    tiny_archive(archive)
    destination = tmp_path / "prepared"
    report = prepare_pm209(archive, destination)
    assert report["questions"] == 3
    corpus = destination / "corpus"
    text = (corpus / "pages.jsonl").read_text()
    for secret in (
        "PRIVATE_QUESTION_SENTINEL",
        "PRIVATE_ANSWER_SENTINEL",
        "official_region",
        "official_qa",
        "qa_data",
        "relevant",
    ):
        assert secret not in text
    pages = list(read_corpus(corpus))
    assert len(pages) == 3
    assert pages[0][0].regions[0].text == "SOURCE_ONLY"
    gold = json.loads((destination / "eval_private/test.jsonl").read_text())
    assert gold["question_index"] == 0 and gold["qa"]["id"] == "official_qa"
    assert gold["regions"][0]["region_id"] == pages[0][0].regions[0].region_id
    with pytest.raises(ValueError):
        list(read_corpus(destination / "eval_private"))
    record = json.loads(text.splitlines()[0])
    record["answer"] = "forbidden"
    (corpus / "pages.jsonl").write_text(json.dumps(record) + "\n")
    manifest = json.loads((corpus / "manifest.json").read_text())
    manifest["pages_sha256"] = hashlib.sha256(
        (corpus / "pages.jsonl").read_bytes()
    ).hexdigest()
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        list(read_corpus(corpus))


def test_pm209_path_failure_leaves_no_published_corpus(tmp_path):
    archive = tmp_path / "bad.zip"
    tiny_archive(archive, unsafe=True)
    with pytest.raises(ValueError):
        prepare_pm209(archive, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def synthetic_pdf():
    """Self-authored PDF with offset CropBox and 90-degree rotation."""
    stream = b"BT /F1 12 Tf 60 80 Td (VISIBLE connect wifi) Tj ET\nBT /F1 8 Tf 2 2 Td (HIDDEN_OUTSIDE_CROP) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /CropBox [20 20 280 180] /Rotate 90 /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def test_i02_rotated_pdf_render_source_and_text(tmp_path):
    from shopguide.ingest.pdf import render_pdf
    from shopguide.ingest.pipeline import import_pdf

    source = tmp_path / "manual.pdf"
    source.write_bytes(synthetic_pdf())
    product = Product(
        product_id="product_pdf",
        domain="demo",
        brand="Synthetic",
        model="PDF",
        category="fixture",
    )
    variant = ProductVariant(variant_id="variant_pdf", product_id=product.product_id)
    result = import_pdf(
        source,
        tmp_path / "runtime",
        "snapshot_pdf",
        "user_alice",
        product,
        variant,
        "Self-authored fixture",
        HashEmbedder(),
    )
    assert result["status"] == "ACTIVE"
    repo = Repository(tmp_path / "runtime/metadata.db")
    try:
        scope = repo.scope("user_alice", "product_pdf", "variant_pdf", "snapshot_pdf")
        index = ScopedIndex(
            repo,
            tmp_path / "runtime/assets",
            tmp_path / "runtime/indexes",
            "snapshot_pdf",
            HashEmbedder(),
        )
        results = index.search("VISIBLE", scope, reranker=RRFReranker(), k=1)
        assert results and "VISIBLE" in results[0]["evidence"].text
        assert all("HIDDEN_OUTSIDE_CROP" not in e.text for e in repo.all(Evidence))
        full = next(e for e in repo.all(Evidence) if e.source_locator.region_id is None)
        rendered = AssetRepository(repo, tmp_path / "runtime/assets").read(
            full.asset_ids[0], scope
        )
        assert rendered == render_pdf(source.read_bytes(), 0)
        with Image.open(io.BytesIO(rendered)) as image:
            assert image.size == (160, 260)
        geometry = json.loads(full.provenance)["pdf_geometry"]
        assert geometry["rotation"] == 90
    finally:
        repo.close()


def test_i06_interrupted_audit_restarts_without_active_pointer(generation):
    _repo, snapshots, evidence, index, _root = generation
    snapshots.transition("snapshot_first", "STAGING", "AUDITING")
    assert snapshots.active("demo") is None
    snapshots.begin("snapshot_first", "demo", {"fixture": 1})
    assert snapshots.get("snapshot_first")["state"] == "STAGING"
    index.build(evidence)
    index.publish(expected_active=None)


def test_single_writer_rejects_concurrent_import(tmp_path):
    import fcntl

    from shopguide.ingest.pipeline import import_corpus

    with (tmp_path / ".writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="writer"):
            import_corpus(
                corpus=tmp_path / "unused",
                root=tmp_path,
                snapshot="snapshot_lock",
                principal="user_alice",
                embedder=HashEmbedder(),
            )
    assert not (tmp_path / "metadata.db").exists()


def test_injected_reranker_must_run_before_final_topk(generation):
    repo, _snapshots, evidence, index, _root = generation
    index.build(evidence)

    class Reverse:
        model_mode = "fake"
        seen = 0

        def scores(self, query, texts, rrf_scores):
            self.seen = len(texts)
            return [float(i) for i in range(len(texts))]

    reranker = Reverse()
    results = index.search("wifi", scope_for(repo), reranker=reranker, k=1)
    assert reranker.seen == 2 and len(results) == 1


def test_pdf_parent_source_corruption_rejected(tmp_path):
    from shopguide.ingest.pipeline import import_pdf
    from shopguide.schemas import Asset

    source = tmp_path / "manual.pdf"
    source.write_bytes(synthetic_pdf())
    root = tmp_path / "runtime"
    import_pdf(
        source,
        root,
        "snapshot_pdf",
        "user_alice",
        Product(
            product_id="product_pdf",
            domain="demo",
            brand="Synthetic",
            model="PDF",
            category="fixture",
        ),
        ProductVariant(variant_id="variant_pdf", product_id="product_pdf"),
        "fixture",
        HashEmbedder(),
    )
    repo = Repository(root / "metadata.db")
    try:
        assets = AssetRepository(repo, root / "assets")
        scope = scope_for(repo, "snapshot_pdf", product="pdf")
        asset = repo.all(Asset)[0]
        assets._path(asset.source_sha256).write_bytes(b"corrupt PDF source")
        with pytest.raises(ValueError, match="source hash"):
            assets.read(asset.asset_id, scope)
    finally:
        repo.close()
