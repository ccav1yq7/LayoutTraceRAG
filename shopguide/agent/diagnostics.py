"""Controlled failure metadata; never exception payloads, credentials or model values."""

import re

from pydantic import ValidationError


def failure_detail(error, stage):
    reason = str(error)
    if not re.fullmatch(
        r"(?:MODEL|INTENT|SPECIALIST|VERIFIER|ORDER)_[A-Z0-9_]{1,80}", reason
    ):
        reason = (
            "SCHEMA_INVALID"
            if isinstance(error, ValidationError)
            else "DEPENDENCY_OR_CONTRACT_FAILED"
        )
    result = {"stage": stage, "exception_type": type(error).__name__, "reason": reason}
    if isinstance(error, ValidationError):
        fields = {
            "updates",
            "ambiguities",
            "goal_id",
            "intent_id",
            "operation",
            "expression",
            "evidence",
            "quote",
            "start",
            "end",
            "attributes",
            "name",
            "value",
            "basis",
            "kind",
            "candidates",
            "question",
            "visual_request",
            "visual_evidence",
            "tool_name",
            "arguments",
            "ticket_id",
            "evidence_ids",
            "specialist",
            "task",
            "reason",
            "missing_fields",
        }
        result["validation_errors"] = [
            {
                "loc": [
                    v if isinstance(v, int) or v in fields else "[unknown-field]"
                    for v in e["loc"]
                ],
                "type": e["type"],
            }
            for e in error.errors(
                include_input=False, include_context=False, include_url=False
            )
        ]
    return result
