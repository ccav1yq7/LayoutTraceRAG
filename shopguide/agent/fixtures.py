import json
from collections import deque

from ..qa.gateway import ExtractiveGateway, GatewayReply
from ..schemas import Cost


class AgentFixtureGateway(ExtractiveGateway):
    """Explicit fake policy responds to observations; optional scripts inject edge cases."""

    def __init__(self, decisions=None):
        self.decisions = deque(decisions or [])

    def complete(self, role, request, images, schema):
        if role == "plan":
            if self.decisions:
                decision = self.decisions.popleft()
            elif not request["product"]:
                decision = {
                    "kind": "clarify",
                    "question": "Which product and variant do you want to use?",
                    "missing_fields": ["product"],
                    "reason": "Confirm product",
                }
            elif any(
                u not in request.get("inspected_upload_ids", [])
                for u in request.get("available_upload_ids", [])
            ):
                uid = next(
                    u
                    for u in request["available_upload_ids"]
                    if u not in request.get("inspected_upload_ids", [])
                )
                decision = {
                    "kind": "tool",
                    "tool_name": "inspect_user_image",
                    "arguments": {"upload_id": uid, "question": request["question"]},
                    "reason": "Inspect the user's attached image",
                }
            elif not request["evidence"]:
                decision = {
                    "kind": "tool",
                    "tool_name": "search_manuals",
                    "arguments_json": json.dumps(
                        {"query": request["question"], "top_k": 8}
                    ),
                    "reason": "Find manual evidence",
                }
            elif not request["inspected_asset_ids"] and any(
                e["asset_ids"] for e in request["evidence"]
            ):
                aid = next(
                    e["asset_ids"][0] for e in request["evidence"] if e["asset_ids"]
                )
                decision = {
                    "kind": "tool",
                    "tool_name": "inspect_asset",
                    "arguments_json": json.dumps(
                        {"asset_id": aid, "question": request["question"]}
                    ),
                    "reason": "Inspect source image",
                }
            else:
                decision = {
                    "kind": "draft_answer",
                    "evidence_ids": [e["evidence_id"] for e in request["evidence"]],
                    "reason": "Draft from retrieved sources",
                }
            if "arguments_json" in decision:
                decision = {
                    **decision,
                    "arguments": json.loads(decision["arguments_json"]),
                }
                decision.pop("arguments_json")
            return GatewayReply(decision, Cost())
        if role == "inspect":
            return GatewayReply(
                {
                    "description": "Synthetic image inspection fixture",
                    "uncertainties": [],
                },
                Cost(image_inputs=len(images)),
            )
        return super().complete(role, request, images, schema)
