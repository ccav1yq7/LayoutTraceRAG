"""Gateway boundary. Deterministic fake outputs are only engineering fixtures."""

from dataclasses import dataclass
from typing import Protocol

from ..schemas import Cost


@dataclass
class GatewayReply:
    payload: dict
    cost: Cost
    usage_available: bool = False


class Gateway(Protocol):
    model_mode: str
    model_id: str

    def complete(
        self, role: str, request: dict, images: dict[str, bytes], schema: dict
    ) -> GatewayReply: ...


class ExtractiveGateway:
    model_mode = "fake"
    model_id = "engineering/extractive-fixture"

    def complete(self, role, request, images, schema):
        payload: dict
        if role == "select":
            payload = {"asset_ids": list(images)[: request["max_assets"]]}
        elif role == "write":
            items = request["evidence"]
            ev = next((e for e in items if e["region_id"] is not None), items[0])
            # Exact source text; no invented procedure and no task-ID lookup.
            selected = [
                aid for aid in images if any(aid in item["asset_ids"] for item in items)
            ]
            refs = [ev["evidence_id"]]
            for asset in selected:
                owner = next(e for e in items if asset in e["asset_ids"])
                if owner["evidence_id"] not in refs:
                    refs.append(owner["evidence_id"])
            payload = {
                "status": "answered",
                "summary": ev["text"],
                "summary_evidence_ids": [ev["evidence_id"]],
                "prerequisites": [],
                "steps": [
                    {
                        "title": "Source excerpt",
                        "text": ev["text"],
                        "evidence_ids": refs,
                        "asset_ids": selected,
                    }
                ],
                "unresolved_items": [],
            }
        elif role == "verify":
            answer = request["answer"]
            payload = {
                "complete": True,
                "claims": [
                    {
                        "claim_id": c["claim_id"],
                        "supported": True,
                        "explanation": "Scripted fixture, not semantic validation",
                    }
                    for c in answer["claims"]
                ],
                "images": [
                    {
                        "step_id": s["step_id"],
                        "asset_id": a,
                        "supported": True,
                        "explanation": "Scripted fixture, not visual validation",
                    }
                    for s in answer["steps"]
                    for a in s["display_asset_ids"]
                ],
            }
        else:
            raise ValueError("unknown gateway role")
        return GatewayReply(payload, Cost(image_inputs=len(images)))
