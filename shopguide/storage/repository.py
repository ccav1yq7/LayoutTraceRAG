from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path
from typing import TypeVar

from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy import create_engine, text

from ..coordinates import pixel_box
from ..schemas import (
    Asset,
    Contract,
    DocumentVersion,
    ManualLocator,
    OrderItem,
    Page,
    Product,
    ProductVariant,
    SearchScope,
)
from .locking import RootLock

T = TypeVar("T", bound=Contract)


class Repository:
    """Trusted backend interface. Never expose grants or scope construction as LLM tools."""

    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self._root_lock = RootLock(database.parent)
        self.engine = create_engine(
            "sqlite:///" + str(database), connect_args={"timeout": 10}
        )
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).parent / "alembic")
        )
        try:
            with self.engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        except BaseException:
            self.close()
            raise

    def close(self):
        self.engine.dispose()
        self._root_lock.close()

    def put(self, record: Contract, record_id: str):
        # Immutable identity: callers cannot silently redirect an old citation.
        fields = {
            "Product": "product_id",
            "ProductVariant": "variant_id",
            "OrderItem": "order_item_id",
            "DocumentVersion": "doc_version_id",
            "Page": "page_id",
            "Asset": "asset_id",
            "Evidence": "evidence_id",
            "Relation": "relation_id",
        }
        kind = type(record).__name__
        if kind not in fields or getattr(record, fields[kind]) != record_id:
            raise ValueError("record identity mismatch")
        args = {"kind": kind, "id": record_id, "payload": record.model_dump_json()}
        with self.engine.begin() as c:
            c.execute(
                text("INSERT OR IGNORE INTO sg_records VALUES (:kind,:id,:payload)"),
                args,
            )
            existing = c.execute(
                text("SELECT payload FROM sg_records WHERE kind=:kind AND id=:id"), args
            ).scalar_one()
            if existing != args["payload"]:
                raise ValueError(
                    "immutable record already exists; create a new version"
                )

    def get(self, cls: type[T], record_id: str) -> T:
        with self.engine.connect() as c:
            payload = c.execute(
                text("SELECT payload FROM sg_records WHERE kind=:kind AND id=:id"),
                {"kind": cls.__name__, "id": record_id},
            ).scalar_one_or_none()
        if payload is None:
            raise KeyError("record not found")
        return cls.model_validate_json(payload)

    def all(self, cls: type[T]) -> list[T]:
        with self.engine.connect() as c:
            rows = (
                c.execute(
                    text("SELECT payload FROM sg_records WHERE kind=:kind ORDER BY id"),
                    {"kind": cls.__name__},
                )
                .scalars()
                .all()
            )
        return [cls.model_validate_json(x) for x in rows]

    def grant(
        self,
        *,
        principal: str,
        product: str,
        variant: str,
        document: str,
        snapshot: str,
        basis: str,
    ):
        if not basis.strip():
            raise ValueError("reviewed applicability basis required")
        p = self.get(Product, product)
        if self.get(ProductVariant, variant).product_id != product:
            raise ValueError("variant belongs to another product")
        if self.get(DocumentVersion, document).status != "active":
            raise ValueError("document is not active")
        # Validate identifiers using the same scope contract.
        SearchScope(
            domain=p.domain,
            principal_id=principal,
            authorized_product_ids=(product,),
            confirmed_variant=variant,
            allowed_doc_version_ids=(document,),
            language_policy=("en",),
            snapshot_id=snapshot,
        )
        with self.engine.begin() as c:
            c.execute(
                text(
                    "INSERT OR IGNORE INTO sg_grants VALUES (:principal,:product,:variant,:document,:snapshot,:domain,:basis)"
                ),
                {
                    "principal": principal,
                    "product": product,
                    "variant": variant,
                    "document": document,
                    "snapshot": snapshot,
                    "domain": p.domain,
                    "basis": basis,
                },
            )

    def scope(
        self, principal: str, product: str, variant: str, snapshot: str
    ) -> SearchScope:
        with self.engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT g.document, g.domain, r.payload FROM sg_grants AS g "
                    "LEFT JOIN sg_records AS r ON r.kind='DocumentVersion' AND r.id=g.document "
                    "WHERE g.principal=:p AND g.product=:q AND g.variant=:v AND g.snapshot=:s ORDER BY g.document"
                ),
                {"p": principal, "q": product, "v": variant, "s": snapshot},
            ).all()
        if any(r[2] is None for r in rows):
            raise KeyError("record not found")
        docs = [DocumentVersion.model_validate_json(r[2]) for r in rows]
        docs = [d for d in docs if d.status == "active"]
        if not docs:
            raise PermissionError("no authorized active documents")
        return SearchScope(
            domain=rows[0][1],
            principal_id=principal,
            authorized_product_ids=(product,),
            confirmed_variant=variant,
            allowed_doc_version_ids=tuple(d.doc_version_id for d in docs),
            language_policy=tuple(sorted({d.language for d in docs})),
            snapshot_id=snapshot,
        )

    def authorize(self, scope: SearchScope):
        # Re-evaluate grants on every read, including when a supplied scope is stale.
        for product in scope.authorized_product_ids:
            current = self.scope(
                scope.principal_id, product, scope.confirmed_variant, scope.snapshot_id
            )
            if scope.domain != current.domain or not set(
                scope.allowed_doc_version_ids
            ) <= set(current.allowed_doc_version_ids):
                raise PermissionError("scope no longer authorized")

    def revoke(self, document_id: str):
        with self.engine.begin() as c:
            row = c.execute(
                text(
                    "SELECT payload FROM sg_records WHERE kind='DocumentVersion' AND id=:id"
                ),
                {"id": document_id},
            ).scalar_one()
            doc = DocumentVersion.model_validate_json(row)
            revoked = doc.model_copy(update={"status": "revoked"})
            c.execute(
                text(
                    "UPDATE sg_records SET payload=:p WHERE kind='DocumentVersion' AND id=:id"
                ),
                {"p": revoked.model_dump_json(), "id": document_id},
            )

    def orders(self, principal: str) -> list[OrderItem]:
        return [o for o in self.all(OrderItem) if o.principal_id == principal]

    def resolve(self, principal: str, query: str) -> list[Product]:
        # Exact model/alias matching only; brand alone returns all owned candidates.
        ids = {o.product_id for o in self.orders(principal)}
        query = query.strip().casefold()
        products = [self.get(Product, i) for i in sorted(ids)]
        return [
            p
            for p in products
            if query
            in {
                p.model.casefold(),
                p.brand.casefold(),
                *(a.casefold() for a in p.aliases),
            }
        ]


class AssetRepository:
    def __init__(
        self, metadata: Repository, root: Path, *, staging_snapshot: str | None = None
    ):
        self.metadata = metadata
        self.staging_snapshot = staging_snapshot
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        # Only validated SHA256 enters filesystem addressing.
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid digest")
        path = self.root / digest[:2] / digest
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("asset path escapes root")
        return path

    def _authorize(self, scope: SearchScope):
        from .snapshots import Snapshots

        self.metadata.authorize(scope)
        snapshots = Snapshots(self.metadata)
        row = snapshots.get(scope.snapshot_id)
        if (
            self.staging_snapshot == scope.snapshot_id
            and row
            and row["state"] in ("STAGING", "AUDITING")
        ):
            return
        snapshots.readable(scope.snapshot_id, allow_legacy=True)

    def store_source(self, source: bytes) -> str:
        digest = hashlib.sha256(source).hexdigest()
        path = self._path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, staged = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(source)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(staged, path)
            except FileExistsError:
                if path.read_bytes() != source:
                    raise ValueError("source hash collision or corruption")
        finally:
            os.unlink(staged)
        return digest

    def add(self, asset: Asset, content: bytes):
        self._authorize(asset.scope)
        if hashlib.sha256(content).hexdigest() != asset.bytes_sha256:
            raise ValueError("asset bytes hash mismatch")
        if len(content) > 20 * 1024 * 1024:
            raise ValueError("asset exceeds byte limit")
        with Image.open(io.BytesIO(content)) as im:
            if im.width * im.height > 25_000_000 or im.size != asset.dimensions:
                raise ValueError("image dimensions mismatch/limit")
            if Image.MIME.get(im.format or "") != asset.mime:
                raise ValueError("MIME mismatch")
            im.verify()
        if not isinstance(asset.source_locator, ManualLocator):
            raise TypeError("asset store supports registered manual sources only")
        loc = asset.source_locator
        doc = self.metadata.get(DocumentVersion, loc.doc_version_id)
        page = self.metadata.get(Page, loc.page_id)
        if (
            doc.content_sha256 != asset.source_sha256
            or page.doc_version_id != doc.doc_version_id
            or page.index_0based != loc.page_index_0based
        ):
            raise ValueError("source version/page/hash mismatch")
        if asset.transform.kind not in ("original", "crop", "resize", "page_render"):
            raise ValueError("transform requires a future ingestion adapter")
        if asset.transform.kind == "original" and asset.dimensions != (
            page.width,
            page.height,
        ):
            raise ValueError("original page dimensions mismatch")
        if asset.transform.kind == "page_render":
            from ..ingest.pdf import render_pdf

            source = self._path(asset.source_sha256).read_bytes()
            if hashlib.sha256(source).hexdigest() != asset.source_sha256:
                raise ValueError("PDF source hash mismatch")
            expected_png = render_pdf(source, loc.page_index_0based)
            if (
                expected_png != content
                or asset.transform.method_version != "pdfium-scale1-v1"
            ):
                raise ValueError("render does not match registered PDF source")
        if asset.transform.parent_asset_id:
            parent = self.metadata.get(Asset, asset.transform.parent_asset_id)
            self.read(parent.asset_id, asset.scope)
            if (
                not isinstance(parent.source_locator, ManualLocator)
                or parent.source_sha256 != asset.source_sha256
                or parent.source_locator.doc_version_id != loc.doc_version_id
                or parent.source_locator.page_id != loc.page_id
            ):
                raise ValueError("parent source mismatch")
            parent_bytes = self.read(parent.asset_id, asset.scope)
            with Image.open(io.BytesIO(parent_bytes)) as original:
                if asset.transform.kind == "crop":
                    if parent.transform.kind not in ("original", "page_render"):
                        raise ValueError(
                            "crops require an original or rendered page parent"
                        )
                    box = pixel_box(loc.bbox_normalized, *parent.dimensions)
                    if asset.transform.original_box != tuple(float(v) for v in box):
                        raise ValueError(
                            "crop coordinates disagree with source locator"
                        )
                    expected = original.crop(box)
                else:
                    if loc != parent.source_locator:
                        raise ValueError("resize must preserve source locator")
                    expected = original.resize(
                        asset.dimensions, Image.Resampling.LANCZOS
                    )
                with Image.open(io.BytesIO(content)) as actual:
                    if (
                        expected.size != actual.size
                        or expected.convert("RGBA").tobytes()
                        != actual.convert("RGBA").tobytes()
                    ):
                        raise ValueError(
                            "derived pixels do not match deterministic transform"
                        )
        path = self._path(asset.bytes_sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Stage then publish without overwriting an existing object.
        fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            try:
                os.link(temp, path)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise ValueError("corrupt content-addressed object")
            self.metadata.put(asset, asset.asset_id)
        finally:
            os.unlink(temp)

    def read(self, asset_id: str, scope: SearchScope) -> bytes:
        self._authorize(scope)
        asset = self.metadata.get(Asset, asset_id)
        owner = asset.scope
        if (
            owner.principal_id != scope.principal_id
            or owner.domain != scope.domain
            or owner.snapshot_id != scope.snapshot_id
            or owner.confirmed_variant != scope.confirmed_variant
            or not set(owner.authorized_product_ids)
            <= set(scope.authorized_product_ids)
            or not set(owner.allowed_doc_version_ids)
            <= set(scope.allowed_doc_version_ids)
        ):
            raise PermissionError("asset outside authorized scope")
        content = self._path(asset.bytes_sha256).read_bytes()
        if hashlib.sha256(content).hexdigest() != asset.bytes_sha256:
            raise ValueError("stored asset hash mismatch")
        source = self._path(asset.source_sha256).read_bytes()
        if hashlib.sha256(source).hexdigest() != asset.source_sha256:
            raise ValueError("stored source hash mismatch")
        if asset.transform.parent_asset_id:
            self.read(asset.transform.parent_asset_id, scope)
        return content
