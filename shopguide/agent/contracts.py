from typing import Literal, TypedDict

from pydantic import Field

from ..schemas import (
    ID,
    AbstainAction,
    Action,
    ClarifyAction,
    Contract,
    DraftAnswerAction,
    HandoffAction,
    Text,
    ToolAction,
)


class AgentBudget(Contract):
    max_planner_steps: int = Field(default=8, ge=1, le=8)
    max_tool_attempts: int = Field(default=12, ge=1, le=12)
    max_model_calls: int = Field(default=12, ge=3, le=12)
    reserved_finalization_calls: Literal[2] = 2
    max_pages: int = Field(default=4, ge=1, le=4)
    max_image_inputs: int = Field(default=8, ge=0, le=8)
    max_reported_tokens: int = Field(default=48000, ge=1)
    deadline_seconds: int = Field(default=90, ge=1, le=90)


class ToolArguments(Contract):
    query: str | None = None
    top_k: int | None = None
    page_id: ID | None = None
    include_neighbors: bool | None = None
    asset_id: ID | None = None
    upload_id: ID | None = None
    question: str | None = None
    reason: str | None = None
    related_order_item: ID | None = None
    summary: str | None = None
    confirmation_id: ID | None = None
    ticket_id: ID | None = None


class PlanDecision(Contract):
    goal_id: Text | None = None
    specialist: Literal["guide", "troubleshoot", "policy"] | None = None
    task: str | None = Field(default=None, max_length=1000)
    kind: Literal["tool", "clarify", "draft_answer", "handoff", "abstain", "delegate"]
    tool_name: (
        Literal[
            "list_purchased_items",
            "resolve_product",
            "search_manuals",
            "read_page",
            "inspect_asset",
            "inspect_user_image",
            "prepare_service_request",
            "commit_service_request",
            "query_service_requests",
        ]
        | None
    ) = None
    arguments: ToolArguments = Field(default_factory=ToolArguments)
    evidence_ids: tuple[ID, ...] = ()
    question: str | None = None
    missing_fields: tuple[Text, ...] = ()
    reason: str | None = Field(default=None, max_length=2000)

    def action(self) -> Action:
        reason = (self.reason or "").strip() or "按当前请求和可用依据处理"
        if self.kind == "delegate":
            raise ValueError("DELEGATION_REQUIRES_SUPERVISOR")
        if self.kind == "tool":
            if self.tool_name is None:
                raise ValueError("tool name required")
            args = self.arguments.model_dump(mode="json", exclude_none=True)
            return ToolAction(
                tool_name=self.tool_name, arguments=args, public_reason=reason
            )
        if self.kind == "clarify":
            return ClarifyAction(
                question=self.question or "",
                missing_fields=self.missing_fields or ("clarification",),
            )
        if self.kind == "draft_answer":
            return DraftAnswerAction(evidence_ids=self.evidence_ids)
        if self.kind == "handoff":
            return HandoffAction(reason=reason, summary_evidence_ids=self.evidence_ids)
        return AbstainAction(reason=reason)


class SearchArgs(Contract):
    query: Text
    top_k: int = Field(default=8, ge=1, le=8)


class CatalogArgs(Contract):
    query: str = ""


class PageArgs(Contract):
    page_id: ID
    include_neighbors: bool = False


class InspectArgs(Contract):
    asset_id: ID
    question: Text


class UploadInspectArgs(Contract):
    upload_id: ID
    question: Text


class ImageObservation(Contract):
    description: str = Field(min_length=1, max_length=2000)
    uncertainties: tuple[Text, ...] = ()


class ServiceArgs(Contract):
    reason: str = Field(min_length=1, max_length=500)
    related_order_item: ID
    summary: str = Field(min_length=1, max_length=2000)


class QueryServiceArgs(Contract):
    ticket_id: ID | None = None


class CommitArgs(Contract):
    confirmation_id: ID


class AgentState(TypedDict, total=False):
    delegation: dict | None
    delegation_count: int
    specialist_reports: list[dict]
    collected_service_query: dict | None
    collected_service_query_args: dict
    fulfilled_business_goals: dict[str, str]
    visual_direct_attempted: bool
    visual_request: str
    media_issues: list[str]
    intent_failure: dict
    intent_candidates: dict
    intent_state: dict | None
    intent_uncertain: bool
    intent_ready: bool
    run_id: str
    session_id: str
    task_id: str
    question: str
    history: list[dict]
    pending: dict | None
    evidence_ids: list[str]
    inspected_asset_ids: list[str]
    observations: list[dict]
    visited_page_ids: list[str]
    planner_steps: int
    tool_attempts: int
    model_calls: int
    image_inputs: int
    reported_tokens: int
    usage_complete: bool
    deadline: float
    last_tool: str | None
    repeats: int
    parse_failures: int
    repair: dict | None
    status: str
    result: dict
    configuration: dict
    missing_information: list[str]
    contradictions: list[str]
    candidate_draft: dict
    verification_observation: dict
    fixed_page_read: bool
    product_id: str | None
    attachment_ids: list[str]
    available_upload_ids: list[str]
    inspected_upload_ids: list[str]
    reply_to_step_id: str | None
    turn_inspected_upload_ids: list[str]
