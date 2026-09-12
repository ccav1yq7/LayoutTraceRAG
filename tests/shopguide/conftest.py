import hashlib
import io

import pytest
from PIL import Image

from shopguide.schemas import (
    Asset,
    BBox,
    DocumentVersion,
    ManualLocator,
    OrderItem,
    Page,
    Product,
    ProductVariant,
    Transform,
)
from shopguide.storage.repository import AssetRepository, Repository


@pytest.fixture
def catalog(tmp_path):
    repo = Repository(tmp_path / "metadata.db")
    out = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(out, format="PNG")
    content = out.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    for i in range(5):
        product = Product(
            product_id=f"product_{i}",
            domain="demo",
            brand="Synthetic",
            model=f"TEST-{100 + i}",
            category="fixture",
        )
        variant = ProductVariant(
            variant_id=f"variant_{i}", product_id=product.product_id
        )
        doc = DocumentVersion(
            doc_version_id=f"doc_{i}",
            source_id=f"source_{i}",
            content_sha256=digest,
            language="en",
            version_label="fixture-v1",
            status="active",
        )
        page = Page(
            page_id=f"page_{i}",
            doc_version_id=doc.doc_version_id,
            index_0based=0,
            width=80,
            height=40,
        )
        for record, key in (
            (product, product.product_id),
            (variant, variant.variant_id),
            (doc, doc.doc_version_id),
            (page, page.page_id),
        ):
            repo.put(record, key)
        user = "user_alice" if i < 3 else "user_bob"
        order = OrderItem(
            order_item_id=f"item_{i}",
            principal_id=user,
            product_id=product.product_id,
            variant_id=variant.variant_id,
            order_id=f"order_{user}",
        )
        repo.put(order, order.order_item_id)
        repo.grant(
            principal=user,
            product=product.product_id,
            variant=variant.variant_id,
            document=doc.doc_version_id,
            snapshot="snapshot_one",
            basis="Self-authored synthetic fixture",
        )
    scope = repo.scope("user_alice", "product_0", "variant_0", "snapshot_one")
    locator = ManualLocator(
        doc_version_id="doc_0",
        page_id="page_0",
        page_index_0based=0,
        bbox_normalized=BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
    )
    asset = Asset(
        asset_id="asset_original",
        source_locator=locator,
        bytes_sha256=digest,
        source_sha256=digest,
        transform=Transform(
            kind="original",
            matrix=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            method_version="fixture-v1",
        ),
        mime="image/png",
        dimensions=(80, 40),
        scope=scope,
    )
    assets = AssetRepository(repo, tmp_path / "assets")
    assets.add(asset, content)
    yield repo, assets, scope, asset, content
    repo.close()
