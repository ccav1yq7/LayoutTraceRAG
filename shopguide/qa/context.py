"""Whitelisted model-visible context, with no principals or private benchmark IDs."""

import json

from ..schemas import Evidence, ManualLocator


def source_page(evidence: Evidence) -> str:
    if not isinstance(evidence.source_locator, ManualLocator):
        raise TypeError("manual evidence required")
    try:
        provenance = json.loads(evidence.provenance)
    except ValueError:
        provenance = {}
    return provenance.get("source_reference") or evidence.source_locator.page_id


def evidence_view(evidence: Evidence):
    locator = evidence.source_locator
    if not isinstance(locator, ManualLocator):
        raise TypeError("manual evidence required")
    try:
        kind = json.loads(evidence.provenance).get("region_kind", "page")
    except ValueError:
        kind = "page"
    return {
        "evidence_id": evidence.evidence_id,
        "text": evidence.text,
        "asset_ids": list(evidence.asset_ids),
        "page_id": source_page(evidence),
        "region_id": locator.region_id,
        "region_kind": kind,
        "bbox_normalized": locator.bbox_normalized.model_dump(mode="json"),
        "page_index_0based": locator.page_index_0based,
    }
