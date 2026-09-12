"""Trusted ingest service. Product applicability is an explicit backend input."""

import hashlib
import io
import json
from pathlib import Path
from typing import Literal, cast

from PIL import Image

from ..coordinates import pixel_box
from ..schemas import (
    Asset,
    BBox,
    DocumentVersion,
    Evidence,
    ManualLocator,
    Page,
    Product,
    ProductVariant,
    Transform,
)
from ..storage.repository import AssetRepository
from ..storage.snapshots import Snapshots
from .contracts import CorpusRegion, Relation
from .pdf import pdf_pages, render_pdf
from .pm209 import opaque

IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


class ManualIngestor:
    def __init__(self, repository, asset_root: Path, snapshot_id: str, domain: str):
        self.repository = repository
        self.snapshot = snapshot_id
        self.domain = domain
        row = Snapshots(repository).get(snapshot_id)
        if not row or row["state"] != "STAGING" or row["domain"] != domain:
            raise ValueError("matching STAGING snapshot required")
        self.assets = AssetRepository(
            repository, asset_root, staging_snapshot=snapshot_id
        )

    def _scope(
        self,
        product: Product,
        variant: ProductVariant,
        principal: str,
        doc: DocumentVersion,
        basis: str,
    ):
        if product.domain != self.domain:
            raise ValueError("product domain mismatch")
        for record, identifier in (
            (product, product.product_id),
            (variant, variant.variant_id),
            (doc, doc.doc_version_id),
        ):
            self.repository.put(record, identifier)
        self.repository.grant(
            principal=principal,
            product=product.product_id,
            variant=variant.variant_id,
            document=doc.doc_version_id,
            snapshot=self.snapshot,
            basis=basis,
        )
        scope = self.repository.scope(
            principal, product.product_id, variant.variant_id, self.snapshot
        )
        # Asset/evidence grant binds to its own source document, not all prior docs.
        return scope.model_copy(
            update={
                "allowed_doc_version_ids": (doc.doc_version_id,),
                "language_policy": (doc.language,),
            }
        )

    def page_image(
        self,
        source: bytes,
        *,
        product: Product,
        variant: ProductVariant,
        principal: str,
        basis: str,
        language: str = "en",
        page_index: int = 0,
        regions: tuple[CorpusRegion, ...] = (),
        source_key: str = "page",
    ):
        digest = hashlib.sha256(source).hexdigest()
        doc = DocumentVersion(
            doc_version_id=opaque("doc", product.product_id, source_key, digest),
            source_id=opaque("source", product.product_id, source_key),
            content_sha256=digest,
            language=language,
            version_label=digest[:12],
            status="active",
        )
        scope = self._scope(product, variant, principal, doc, basis)
        return self._page(
            source,
            doc,
            scope,
            page_index,
            regions,
            "original",
            "source-bytes-v1",
            source_reference=source_key,
        )

    def _page(
        self,
        image_bytes,
        doc,
        scope,
        index,
        regions,
        kind,
        method,
        geometry=None,
        source_reference=None,
    ):
        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
            mime = cast(
                Literal["image/png", "image/jpeg", "image/webp"],
                Image.MIME[image.format or ""],
            )
            image.verify()
        page_id = opaque("page", doc.doc_version_id, str(index))
        self.repository.put(
            Page(
                page_id=page_id,
                doc_version_id=doc.doc_version_id,
                index_0based=index,
                width=width,
                height=height,
                rotation=0,
            ),
            page_id,
        )
        locator = ManualLocator(
            doc_version_id=doc.doc_version_id,
            page_id=page_id,
            page_index_0based=index,
            bbox_normalized=BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
        )
        asset = Asset(
            asset_id=opaque(
                "asset", self.snapshot, scope.principal_id, page_id, "full"
            ),
            source_locator=locator,
            bytes_sha256=hashlib.sha256(image_bytes).hexdigest(),
            source_sha256=doc.content_sha256,
            transform=Transform(
                kind=kind,
                matrix=tuple(geometry["source_to_pixel_matrix"])
                if geometry
                else IDENTITY,
                method_version=method,
            ),
            mime=mime,
            dimensions=(width, height),
            scope=scope,
        )
        self.assets.add(asset, image_bytes)
        page_text = (
            "\n".join(r.text for r in regions if r.text.strip())
            or "[Page has no extracted text]"
        )
        full = Evidence(
            evidence_id=opaque("ev", asset.asset_id),
            kind="manual",
            source_locator=locator,
            text=page_text,
            asset_ids=(asset.asset_id,),
            scope=scope,
            provenance=json.dumps(
                {
                    "method": "registered-source",
                    "pdf_geometry": geometry,
                    "source_reference": source_reference,
                },
                sort_keys=True,
            ),
        )
        self.repository.put(full, full.evidence_id)
        evidence = [full]
        region_pairs = []
        for region in regions:
            box = pixel_box(region.bbox, width, height)
            with Image.open(io.BytesIO(image_bytes)) as image:
                crop = image.crop(box)
                out = io.BytesIO()
                crop.save(out, format="PNG")
                raw = out.getvalue()
            loc = locator.model_copy(
                update={"region_id": region.region_id, "bbox_normalized": region.bbox}
            )
            derived = Asset(
                asset_id=opaque("asset", asset.asset_id, region.region_id),
                source_locator=loc,
                bytes_sha256=hashlib.sha256(raw).hexdigest(),
                source_sha256=doc.content_sha256,
                transform=Transform(
                    kind="crop",
                    parent_asset_id=asset.asset_id,
                    original_box=(
                        float(box[0]),
                        float(box[1]),
                        float(box[2]),
                        float(box[3]),
                    ),
                    matrix=(
                        1.0,
                        0.0,
                        float(-box[0]),
                        0.0,
                        1.0,
                        float(-box[1]),
                        0.0,
                        0.0,
                        1.0,
                    ),
                    method_version="pillow-crop-v1",
                ),
                mime="image/png",
                dimensions=crop.size,
                scope=scope,
            )
            self.assets.add(derived, raw)
            ev = Evidence(
                evidence_id=opaque("ev", derived.asset_id),
                kind="manual",
                source_locator=loc,
                text=region.text.strip() or f"[{region.kind}: no extracted text]",
                asset_ids=(derived.asset_id,),
                scope=scope,
                provenance=json.dumps(
                    {
                        "region_kind": region.kind,
                        "source_xywh": region.original_xywh,
                        "source_reference": source_reference,
                        "method": "source-layout-v1",
                    }
                ),
            )
            self.repository.put(ev, ev.evidence_id)
            evidence.append(ev)
            region_pairs.append((region, ev))
            self._relation(
                ev, full, "belongs_to_page", "Source page membership", inferred=False
            )
        text_regions = [
            (r, e)
            for r, e in region_pairs
            if r.kind in ("Text", "Title") and r.text.strip()
        ]
        for region, ev in region_pairs:
            if region.kind in ("Text", "Title") or not text_regions:
                continue
            near = min(
                text_regions, key=lambda pair: abs(pair[0].bbox.y0 - region.bbox.y1)
            )
            self._relation(
                ev,
                near[1],
                "nearby_text",
                "Vertical layout proximity only; not a verified caption",
                inferred=True,
            )
        return evidence

    def _relation(self, source, target, kind, basis, inferred):
        relation = Relation(
            relation_id=opaque("rel", source.evidence_id, target.evidence_id, kind),
            source_evidence_id=source.evidence_id,
            target_evidence_id=target.evidence_id,
            kind=kind,
            basis=basis,
            inferred=inferred,
        )
        self.repository.put(relation, relation.relation_id)

    def pdf(
        self,
        source: bytes,
        *,
        product: Product,
        variant: ProductVariant,
        principal: str,
        basis: str,
        language: str = "en",
    ):
        digest = self.assets.store_source(source)
        doc = DocumentVersion(
            doc_version_id=opaque("doc", product.product_id, digest),
            source_id=opaque("source", product.product_id, "pdf"),
            content_sha256=digest,
            language=language,
            version_label=digest[:12],
            status="active",
        )
        scope = self._scope(product, variant, principal, doc, basis)
        result = []
        for index, text, geometry in pdf_pages(source):
            png = render_pdf(source, index)
            with Image.open(io.BytesIO(png)) as im:
                w, h = im.size
            region = CorpusRegion(
                region_id=opaque("region", doc.doc_version_id, str(index)),
                kind="Text",
                text=text,
                original_xywh=(0.0, 0.0, float(w), float(h)),
                bbox=BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
            )
            result.extend(
                self._page(
                    png,
                    doc,
                    scope,
                    index,
                    (region,),
                    "page_render",
                    "pdfium-scale1-v1",
                    geometry,
                )
            )
        if not result:
            raise ValueError("PDF contains no pages")
        return result
