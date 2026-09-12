"""One local generation: ingest, build, audit, then atomically publish."""

import fcntl
import hashlib
import inspect
import json
from functools import wraps
from pathlib import Path

from ..retrieval.store import ScopedIndex
from ..schemas import Product, ProductVariant
from ..storage.repository import Repository
from ..storage.snapshots import Snapshots
from .manuals import ManualIngestor
from .pm209 import opaque, read_corpus


def single_writer(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        root = inspect.signature(function).bind(*args, **kwargs).arguments["root"]
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".writer.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("another ingestion writer is active") from None
            try:
                return function(*args, **kwargs)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    return guarded


@single_writer
def import_corpus(
    corpus: Path,
    root: Path,
    snapshot: str,
    principal: str,
    embedder,
    *,
    max_pages: int | None = None,
    manual_ids: list[str] | None = None,
):
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be positive")
    # Validate all schema/paths/hash before staging or granting anything.
    pages = list(read_corpus(corpus))
    if manual_ids is not None:
        if (
            not manual_ids
            or len(manual_ids) != len(set(manual_ids))
            or not set(manual_ids) <= {p.manual_id for p, _ in pages}
        ):
            raise ValueError("unknown/empty/duplicate manual selection")
        pages = [(p, image) for p, image in pages if p.manual_id in manual_ids]
    manifest = json.loads((corpus / "manifest.json").read_text())
    identity = {
        "kind": "pm209-pages-v1",
        "corpus_sha256": manifest["pages_sha256"],
        "principal": principal,
        "max_pages": max_pages,
        "manual_ids": sorted(manual_ids) if manual_ids is not None else None,
        "embedding": embedder.identity.model_dump(mode="json"),
    }
    repo = Repository(root / "metadata.db")
    snapshots = Snapshots(repo)
    started = False
    try:
        previous = snapshots.active("pm209")
        snapshots.begin(snapshot, "pm209", identity)
        started = True
        ingestor = ManualIngestor(repo, root / "assets", snapshot, "pm209")
        evidence = []
        products = set()
        for page, path in pages[:max_pages]:
            source = path.read_bytes()
            if hashlib.sha256(source).hexdigest() != page.image_sha256:
                raise ValueError("source image hash mismatch")
            # These identify manuals only; never fabricate real brand/SKU/firmware.
            product = Product(
                product_id=page.manual_id,
                domain="pm209",
                brand="unknown",
                model=page.manual_id,
                category="manual-only identity",
            )
            variant = ProductVariant(
                variant_id=opaque("variant", page.manual_id),
                product_id=product.product_id,
            )
            evidence.extend(
                ingestor.page_image(
                    source,
                    product=product,
                    variant=variant,
                    principal=principal,
                    basis="PM209 official source-manual membership; no real SKU/variant assertion",
                    page_index=page.index_0based,
                    regions=page.regions,
                    source_key=page.page_id,
                )
            )
            products.add((product.product_id, variant.variant_id))
        index = ScopedIndex(repo, root / "assets", root / "indexes", snapshot, embedder)
        audit = index.build(evidence)
        index.publish(expected_active=previous)
        return {
            "snapshot_id": snapshot,
            "status": "ACTIVE",
            "model_mode": embedder.identity.model_mode,
            "pages_imported": len(pages[:max_pages]),
            "products": sorted(products),
            "audit": audit,
            "official_benchmark": False,
        }
    except BaseException:
        if started:
            snapshots.fail(snapshot)
        raise
    finally:
        repo.close()


@single_writer
def import_pdf(
    source_path: Path,
    root: Path,
    snapshot: str,
    principal: str,
    product: Product,
    variant: ProductVariant,
    basis: str,
    embedder,
):
    source = source_path.read_bytes()
    identity = {
        "kind": "pdf-v1",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "principal": principal,
        "product": product.model_dump(mode="json"),
        "variant": variant.model_dump(mode="json"),
        "basis": basis,
        "embedding": embedder.identity.model_dump(mode="json"),
    }
    repo = Repository(root / "metadata.db")
    snapshots = Snapshots(repo)
    started = False
    try:
        previous = snapshots.active(product.domain)
        snapshots.begin(snapshot, product.domain, identity)
        started = True
        ingestor = ManualIngestor(repo, root / "assets", snapshot, product.domain)
        evidence = ingestor.pdf(
            source, product=product, variant=variant, principal=principal, basis=basis
        )
        index = ScopedIndex(repo, root / "assets", root / "indexes", snapshot, embedder)
        audit = index.build(evidence)
        index.publish(expected_active=previous)
        return {
            "snapshot_id": snapshot,
            "status": "ACTIVE",
            "model_mode": embedder.identity.model_mode,
            "audit": audit,
            "official_benchmark": False,
        }
    except BaseException:
        if started:
            snapshots.fail(snapshot)
        raise
    finally:
        repo.close()
