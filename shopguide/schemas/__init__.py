"""Strict V2 contracts; authority is established by repositories, never by a model."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ID = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,95}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SHA256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Domain = Literal["demo", "pm209", "ecom"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, allow_inf_nan=False
    )


class Product(Contract):
    product_id: ID
    domain: Domain
    brand: Text
    model: Text
    aliases: tuple[Text, ...] = ()
    alias_basis: Text | None = None
    category: Text

    @model_validator(mode="after")
    def traced_aliases(self):
        if self.aliases and not self.alias_basis:
            raise ValueError("aliases require a documented basis")
        return self


class ProductVariant(Contract):
    variant_id: ID
    product_id: ID
    market: Text | None = None
    hardware_revision: Text | None = None
    firmware_range: Text | None = None


class OrderItem(Contract):
    order_item_id: ID
    principal_id: ID
    product_id: ID
    variant_id: ID
    order_id: ID


class DocumentVersion(Contract):
    doc_version_id: ID
    source_id: ID
    content_sha256: SHA256
    language: Text
    version_label: Text
    status: Literal["staging", "active", "revoked"] = "staging"


class BBox(Contract):
    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def area(self):
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("bbox must have positive area")
        return self


class ManualLocator(Contract):
    type: Literal["manual"] = "manual"
    doc_version_id: ID
    page_id: ID
    page_index_0based: int = Field(ge=0)
    page_label: Text | None = None
    region_id: ID | None = None
    bbox_normalized: BBox


class VideoLocator(Contract):
    type: Literal["video"] = "video"
    video_version_id: ID
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    frame_timestamp_s: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def interval(self):
        if not self.start_s <= self.end_s <= self.duration_s:
            raise ValueError("invalid video interval")
        if (
            self.frame_timestamp_s is not None
            and not self.start_s <= self.frame_timestamp_s <= self.end_s
        ):
            raise ValueError("frame outside interval")
        return self


class BusinessLocator(Contract):
    type: Literal["business"] = "business"
    domain: Domain
    actual_tool_call_id: ID
    entity_ref: ID
    observed_at: datetime


class UploadLocator(Contract):
    type: Literal["user_upload"] = "user_upload"
    upload_id: ID
    principal_id: ID
    bytes_sha256: SHA256


Locator = Annotated[
    ManualLocator | VideoLocator | BusinessLocator | UploadLocator,
    Field(discriminator="type"),
]


class Page(Contract):
    page_id: ID
    doc_version_id: ID
    index_0based: int = Field(ge=0)
    label: Text | None = None
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    rotation: Literal[0, 90, 180, 270] = 0


class SearchScope(Contract):
    domain: Domain
    principal_id: ID
    authorized_product_ids: tuple[ID, ...] = Field(min_length=1)
    confirmed_variant: ID
    allowed_doc_version_ids: tuple[ID, ...] = Field(min_length=1)
    language_policy: tuple[Text, ...] = Field(min_length=1)
    snapshot_id: ID


class Transform(Contract):
    kind: Literal["original", "page_render", "crop", "resize", "video_frame"]
    parent_asset_id: ID | None = None
    # Original coordinate units and homogeneous transform retained for audit.
    original_box: tuple[float, float, float, float] | None = None
    matrix: tuple[float, float, float, float, float, float, float, float, float]
    method_version: Text

    @model_validator(mode="after")
    def lineage(self):
        if self.kind in ("crop", "resize") and self.parent_asset_id is None:
            raise ValueError("derived image requires parent asset")
        if self.kind == "crop" and self.original_box is None:
            raise ValueError("crop requires original coordinates")
        if self.kind == "original" and self.parent_asset_id is not None:
            raise ValueError("original cannot have a parent asset")
        return self


class Asset(Contract):
    asset_id: ID
    source_locator: Locator
    bytes_sha256: SHA256
    source_sha256: SHA256
    transform: Transform
    mime: Literal["image/png", "image/jpeg", "image/webp"]
    dimensions: tuple[int, int]
    scope: SearchScope

    @model_validator(mode="after")
    def source(self):
        if min(self.dimensions) <= 0:
            raise ValueError("invalid image dimensions")
        if (
            self.transform.kind == "original"
            and self.bytes_sha256 != self.source_sha256
        ):
            raise ValueError("original hash mismatch")
        if (
            isinstance(self.source_locator, ManualLocator)
            and self.source_locator.doc_version_id
            not in self.scope.allowed_doc_version_ids
        ):
            raise ValueError("source version outside scope")
        if isinstance(self.source_locator, UploadLocator) and (
            self.source_locator.principal_id != self.scope.principal_id
            or self.source_locator.bytes_sha256 != self.source_sha256
        ):
            raise ValueError("upload owner/hash mismatch")
        return self


class Evidence(Contract):
    evidence_id: ID
    kind: Literal["manual", "video", "business", "user_upload"]
    source_locator: Locator
    text: Text
    asset_ids: tuple[ID, ...] = ()
    scope: SearchScope
    provenance: Text

    @model_validator(mode="after")
    def consistent_kind(self):
        if self.kind != self.source_locator.type:
            raise ValueError("evidence kind differs from locator")
        if (
            isinstance(self.source_locator, ManualLocator)
            and self.source_locator.doc_version_id
            not in self.scope.allowed_doc_version_ids
        ):
            raise ValueError("evidence version outside scope")
        return self


class ToolAction(Contract):
    kind: Literal["tool"] = "tool"
    tool_name: ID
    arguments: dict[str, Any]
    public_reason: Text


class ClarifyAction(Contract):
    kind: Literal["clarify"] = "clarify"
    question: Text
    missing_fields: tuple[Text, ...] = Field(min_length=1)
    choices: tuple[Text, ...] = ()


class DraftAnswerAction(Contract):
    kind: Literal["draft_answer"] = "draft_answer"
    evidence_ids: tuple[ID, ...] = Field(min_length=1)
    requested_format: Literal["steps"] = "steps"


class HandoffAction(Contract):
    kind: Literal["handoff"] = "handoff"
    reason: Text
    summary_evidence_ids: tuple[ID, ...] = ()


class AbstainAction(Contract):
    kind: Literal["abstain"] = "abstain"
    reason: Text
    missing_evidence: tuple[Text, ...] = ()


Action = Annotated[
    ToolAction | ClarifyAction | DraftAnswerAction | HandoffAction | AbstainAction,
    Field(discriminator="kind"),
]

ErrorCode = Literal[
    "SCOPE_REQUIRED",
    "PRODUCT_AMBIGUOUS",
    "FORBIDDEN",
    "SOURCE_VERSION_MISMATCH",
    "ASSET_NOT_FOUND",
    "UNSUPPORTED_FORMAT",
    "TIMEOUT",
    "RATE_LIMITED",
    "BUDGET_EXCEEDED",
    "CONFIRMATION_REQUIRED",
    "MODEL_OUTPUT_INVALID",
    "INDEX_IDENTITY_MISMATCH",
    "INVALID_ARGUMENTS",
    "UNKNOWN_TOOL",
    "INTERNAL_ERROR",
]


class ToolError(Contract):
    code: ErrorCode
    message: Text
    retryable: bool = False


class Cost(Contract):
    latency_ms: float = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    image_inputs: int = Field(default=0, ge=0)


class ToolResult(Contract):
    schema_version: Literal[1] = 1
    call_id: ID
    status: Literal["ok", "empty", "partial", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: tuple[ID, ...] = ()
    asset_ids: tuple[ID, ...] = ()
    warnings: tuple[Text, ...] = ()
    error: ToolError | None = None
    cost: Cost = Field(default_factory=Cost)

    @model_validator(mode="after")
    def consistent_status(self):
        if (self.status == "error") != (self.error is not None):
            raise ValueError("error status and object must agree")
        return self


class Claim(Contract):
    claim_id: ID
    kind: Literal[
        "manual_instruction", "user_observation", "business_state", "inference"
    ]
    text: Text
    evidence_ids: tuple[ID, ...] = Field(min_length=1)


class GuideStep(Contract):
    step_id: ID
    title: Text
    text: Text
    claim_ids: tuple[ID, ...] = Field(min_length=1)
    evidence_ids: tuple[ID, ...] = Field(min_length=1)
    display_asset_ids: tuple[ID, ...] = ()


class Citation(Contract):
    evidence_id: ID
    source_locator: Locator


class Verification(Contract):
    structural: Literal["pass", "fail"] = "fail"
    semantic: Literal["supported", "partial", "unsupported", "not_checked"] = (
        "not_checked"
    )
    warnings: tuple[Text, ...] = ()


class GuideAnswer(Contract):
    schema_version: Literal[2] = 2
    status: Literal[
        "answered", "partial", "needs_clarification", "abstained", "handoff"
    ]
    product_ref: ID
    source_snapshot_id: ID
    summary: Text
    prerequisites: tuple[Text, ...] = ()
    steps: tuple[GuideStep, ...] = ()
    claims: tuple[Claim, ...] = ()
    unresolved_items: tuple[Text, ...] = ()
    followup_question: Text | None = None
    citations: tuple[Citation, ...] = ()
    verification: Verification = Field(default_factory=Verification)

    @model_validator(mode="after")
    def references(self):
        for items, field in (
            (self.steps, "step_id"),
            (self.claims, "claim_id"),
            (self.citations, "evidence_id"),
        ):
            ids = [getattr(x, field) for x in items]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate answer ID")
        claims = {c.claim_id: c for c in self.claims}
        cited = {c.evidence_id for c in self.citations}
        if any(not set(c.evidence_ids) <= cited for c in self.claims):
            raise ValueError("uncited claim")
        for step in self.steps:
            if (
                not set(step.claim_ids) <= claims.keys()
                or not set(step.evidence_ids) <= cited
            ):
                raise ValueError("unresolved step references")
            if any(
                not set(claims[c].evidence_ids) <= set(step.evidence_ids)
                for c in step.claim_ids
            ):
                raise ValueError("claim evidence missing from step")
        return self
