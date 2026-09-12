import hashlib
import io

import pytest
from PIL import Image

from shopguide.schemas import Asset, BBox, DocumentVersion, Transform
from shopguide.storage.repository import AssetRepository, Repository


def test_u02_catalog_and_principal_isolation(catalog):
    repo, assets, scope, asset, _content = catalog
    assert len(repo.resolve("user_alice", "Synthetic")) == 3
    assert [p.model for p in repo.resolve("user_alice", "TEST-100")] == ["TEST-100"]
    assert repo.resolve("user_alice", "TEST-10") == []
    assert repo.resolve("user_bob", "TEST-100") == []
    with pytest.raises(PermissionError):
        repo.scope("user_bob", "product_0", "variant_0", "snapshot_one")
    bob = repo.scope("user_bob", "product_3", "variant_3", "snapshot_one")
    with pytest.raises(PermissionError):
        assets.read(asset.asset_id, bob)
    with pytest.raises(PermissionError):
        assets.read(
            asset.asset_id, scope.model_copy(update={"principal_id": "user_bob"})
        )


def test_u06_asset_reopen_hash_and_version(catalog, tmp_path):
    _repo, assets, scope, asset, content = catalog
    reopened = Repository(tmp_path / "metadata.db")
    try:
        assert (
            AssetRepository(reopened, tmp_path / "assets").read(asset.asset_id, scope)
            == content
        )
        doc = reopened.get(DocumentVersion, "doc_0")
        with pytest.raises(ValueError, match="immutable"):
            reopened.put(doc.model_copy(update={"content_sha256": "b" * 64}), "doc_0")
        assert (
            reopened.get(DocumentVersion, "doc_0").content_sha256 == asset.source_sha256
        )
        with pytest.raises(ValueError, match="hash"):
            assets.add(asset, b"fake image")
        assets._path(asset.bytes_sha256).write_bytes(b"corrupted")
        with pytest.raises(ValueError, match="hash"):
            assets.read(asset.asset_id, scope)
    finally:
        reopened.close()


def test_u06_crop_lineage(catalog):
    repo, assets, scope, asset, content = catalog
    out = io.BytesIO()
    with Image.open(io.BytesIO(content)) as im:
        im.crop((0, 0, 40, 40)).save(out, format="PNG")
    cropped = out.getvalue()
    loc = asset.source_locator.model_copy(
        update={
            "region_id": "region_left",
            "bbox_normalized": BBox(x0=0.0, y0=0.0, x1=0.5, y1=1.0),
        }
    )
    derived = Asset(
        asset_id="asset_crop",
        source_locator=loc,
        bytes_sha256=hashlib.sha256(cropped).hexdigest(),
        source_sha256=asset.source_sha256,
        mime="image/png",
        dimensions=(40, 40),
        scope=scope,
        transform=Transform(
            kind="crop",
            parent_asset_id=asset.asset_id,
            original_box=(0.0, 0.0, 40.0, 40.0),
            matrix=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            method_version="pillow-fixture",
        ),
    )
    assets.add(derived, cropped)
    assert assets.read(derived.asset_id, scope) == cropped
    assert (
        repo.get(Asset, derived.transform.parent_asset_id).bytes_sha256
        == asset.source_sha256
    )
    with pytest.raises(ValueError):
        assets.add(derived.model_copy(update={"source_sha256": "e" * 64}), cropped)


def test_u06_stale_scope_after_revocation(catalog):
    repo, assets, scope, asset, _content = catalog
    repo.revoke("doc_0")
    with pytest.raises(PermissionError):
        assets.read(asset.asset_id, scope)


def test_asset_path_and_mime(catalog):
    _repo, assets, _scope, asset, content = catalog
    with pytest.raises(ValueError):
        assets._path("../anything")
    with pytest.raises(ValueError, match="MIME"):
        assets.add(asset.model_copy(update={"mime": "image/jpeg"}), content)
    with pytest.raises(ValueError, match="page"):
        assets.add(
            asset.model_copy(
                update={
                    "source_locator": asset.source_locator.model_copy(
                        update={"page_index_0based": 1}
                    )
                }
            ),
            content,
        )


def test_scope_product_snapshot_and_domain(catalog):
    _repo, assets, scope, asset, _content = catalog
    for update in (
        {"domain": "pm209"},
        {"snapshot_id": "snapshot_two"},
        {"authorized_product_ids": ("product_1",)},
        {"allowed_doc_version_ids": ("doc_1",)},
        {"confirmed_variant": "variant_1"},
    ):
        with pytest.raises(PermissionError):
            assets.read(asset.asset_id, scope.model_copy(update=update))


def test_u06_fabricated_crop_rejected(catalog):
    _repo, assets, scope, asset, _content = catalog
    out = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(out, format="PNG")
    fabricated = out.getvalue()
    loc = asset.source_locator.model_copy(
        update={"bbox_normalized": BBox(x0=0.0, y0=0.0, x1=0.5, y1=1.0)}
    )
    candidate = Asset(
        asset_id="asset_fakecrop",
        source_locator=loc,
        bytes_sha256=hashlib.sha256(fabricated).hexdigest(),
        source_sha256=asset.source_sha256,
        mime="image/png",
        dimensions=(40, 40),
        scope=scope,
        transform=Transform(
            kind="crop",
            parent_asset_id=asset.asset_id,
            original_box=(0.0, 0.0, 40.0, 40.0),
            matrix=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            method_version="fixture",
        ),
    )
    with pytest.raises(ValueError, match="derived pixels"):
        assets.add(candidate, fabricated)
