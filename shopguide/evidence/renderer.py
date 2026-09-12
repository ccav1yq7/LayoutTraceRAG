import html
import re

from ..schemas import Evidence, GuideAnswer, SearchScope, Verification
from ..storage.repository import AssetRepository


def render_answer(
    answer: GuideAnswer,
    scope: SearchScope,
    evidence: dict[str, Evidence],
    assets: AssetRepository,
) -> dict:
    """Return text-only structured fields and opaque assets after L1 checks.

    Semantic support remains not_checked regardless of what the draft claims.
    Consumers must render strings as text nodes, never raw HTML/Markdown.
    """
    assets.metadata.authorize(scope)
    if (
        answer.product_ref not in scope.authorized_product_ids
        or answer.source_snapshot_id != scope.snapshot_id
    ):
        raise PermissionError("answer outside scope")
    for citation in answer.citations:
        ev = evidence[citation.evidence_id]
        registered = assets.metadata.get(Evidence, citation.evidence_id)
        if ev != registered:
            raise ValueError("evidence differs from registered record")
        if (
            ev.scope.principal_id != scope.principal_id
            or ev.scope.domain != scope.domain
            or ev.scope.snapshot_id != scope.snapshot_id
            or ev.scope.confirmed_variant != scope.confirmed_variant
            or not set(ev.scope.authorized_product_ids)
            <= set(scope.authorized_product_ids)
            or not set(ev.scope.allowed_doc_version_ids)
            <= set(scope.allowed_doc_version_ids)
            or ev.source_locator != citation.source_locator
        ):
            raise PermissionError("citation outside scope or version mismatch")
    for claim in answer.claims:
        if claim.kind == "manual_instruction" and any(
            evidence[e].kind != "manual" for e in claim.evidence_ids
        ):
            raise ValueError("instructions require manual evidence")
    for step in answer.steps:
        allowed = {a for e in step.evidence_ids for a in evidence[e].asset_ids}
        if not set(step.display_asset_ids) <= allowed:
            raise ValueError("display assets not supported by step evidence")
        for asset_id in step.display_asset_ids:
            assets.read(asset_id, scope)
    result = answer.model_dump(mode="json")
    result["verification"] = Verification(
        structural="pass", semantic="not_checked"
    ).model_dump(mode="json")

    def plain(value):
        if isinstance(value, str):
            decoded = html.unescape(value)
            if re.search(r"<[^>]+>|!\[.*?\]\(", decoded):
                raise ValueError("HTML/Markdown images are not accepted")
            return value
        if isinstance(value, list):
            return [plain(v) for v in value]
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        return value

    return plain(result)
