from typing import Literal

from pydantic import Field, model_validator

from ..schemas import ID, Contract, Text

Profile = Literal["pm209-given-page", "pm209-retrieved-top1", "pm209-multipage"]


class RAGBudget(Contract):
    max_pages: int = Field(default=4, ge=1, le=4)
    max_evidence: int = Field(default=32, ge=1, le=64)
    max_candidate_images: int = Field(default=4, ge=1, le=4)
    max_display_images: int = Field(default=2, ge=0, le=2)
    max_model_calls: int = Field(default=3, ge=1, le=3)
    max_image_inputs: int = Field(default=8, ge=0, le=8)


class RegionSelection(Contract):
    asset_ids: tuple[ID, ...] = ()


class DraftClaim(Contract):
    text: Text
    evidence_ids: tuple[ID, ...] = Field(min_length=1)


class DraftStep(Contract):
    title: Text
    text: Text
    evidence_ids: tuple[ID, ...] = Field(min_length=1)
    asset_ids: tuple[ID, ...] = ()


class WriterDraft(Contract):
    status: Literal["answered", "partial", "abstained"]
    summary: Text
    summary_evidence_ids: tuple[ID, ...] = ()
    prerequisites: tuple[DraftClaim, ...] = ()
    steps: tuple[DraftStep, ...] = ()
    unresolved_items: tuple[Text, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def grounded(self):
        if self.status != "abstained" and not self.summary_evidence_ids:
            raise ValueError("answer summary needs evidence")
        if self.status == "abstained" and (
            self.steps or self.prerequisites or self.summary_evidence_ids
        ):
            raise ValueError("abstention must not contain instructions")
        return self


class ClaimCheck(Contract):
    claim_id: ID
    supported: bool
    explanation: Text


class ImageCheck(Contract):
    step_id: ID
    asset_id: ID
    supported: bool
    explanation: Text


class SemanticVerdict(Contract):
    claims: tuple[ClaimCheck, ...]
    images: tuple[ImageCheck, ...]
    complete: bool
    incomplete_reason: (
        Literal["none", "review_incomplete", "answer_incomplete", "evidence_uncertain"]
        | None
    ) = None

    @model_validator(mode="after")
    def consistent_completion(self):
        if self.incomplete_reason is not None and self.complete != (
            self.incomplete_reason == "none"
        ):
            raise ValueError("verification completion/reason disagree")
        return self


class PMRequest(Contract):
    question_id: ID
    manual_id: ID
    question: str = Field(min_length=1)
    split: Literal["train", "val", "test"]
    protocol: Profile
    given_page_id: ID | None = None

    @model_validator(mode="after")
    def given_page(self):
        if not self.question.strip():
            raise ValueError("question cannot be blank")
        if (self.protocol == "pm209-given-page") != (self.given_page_id is not None):
            raise ValueError("only given-page requests may supply a known page")
        return self


class DeliveredServiceQuery(Contract):
    """Backend query facts rendered separately, never manual evidence/citations."""

    source_tool: Literal["query_service_requests"] = "query_service_requests"
    simulated: Literal[True] = True
    status: Literal["ok", "empty"]
    ticket_ids: tuple[ID, ...] = Field(default=(), max_length=20)
    has_more: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if (self.status == "ok") != bool(self.ticket_ids):
            raise ValueError("SERVICE_QUERY_STATUS_INCONSISTENT")
        if len(set(self.ticket_ids)) != len(self.ticket_ids) or (
            self.has_more and not self.ticket_ids
        ):
            raise ValueError("SERVICE_QUERY_ITEMS_INVALID")
        return self
