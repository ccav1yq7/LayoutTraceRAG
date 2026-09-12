import copy
import hashlib
import json
import time


class BudgetStop(RuntimeError):
    pass


class MeteredGateway:
    """One shared ledger for planner, image tools and final Writer/Verifier calls."""

    def __init__(self, inner, sessions, principal, state, budget):
        self.inner = inner
        self.sessions = sessions
        self.principal = principal
        self.state = state
        self.budget = budget
        self.model_id = inner.model_id
        self.model_mode = inner.model_mode
        self.supports_images = getattr(inner, "supports_images", True)
        self.provider = getattr(inner, "provider", "fixture")
        self.finalizing = False

    def guard(self):
        row = self.sessions.run(self.state["run_id"], self.principal)
        if row["cancelled"]:
            raise BudgetStop("CANCELLED")
        if time.time() >= self.state["deadline"]:
            raise BudgetStop("DEADLINE_EXCEEDED")
        if self.state["reported_tokens"] > self.budget.max_reported_tokens:
            raise BudgetStop("TOKEN_BUDGET_EXCEEDED")

    def complete(self, role, request, images, schema):
        self.guard()
        reserve = 0 if self.finalizing else self.budget.reserved_finalization_calls
        if self.state["model_calls"] + reserve >= self.budget.max_model_calls:
            raise BudgetStop("MODEL_BUDGET_RESERVED")
        if self.state["image_inputs"] + len(images) > self.budget.max_image_inputs:
            raise BudgetStop("IMAGE_BUDGET_EXCEEDED")
        payload = json.dumps(request, sort_keys=True, ensure_ascii=False)
        if len(payload) > 48000:
            raise BudgetStop("CONTEXT_BUDGET_EXCEEDED")
        self.state["model_calls"] += 1
        self.state["image_inputs"] += len(images)
        self.sessions.save(self.state["run_id"], self.principal, self.state)
        self.sessions.event(
            self.state["run_id"],
            "model.started",
            {
                "role": role,
                "ordinal": self.state["model_calls"],
                "request_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "image_inputs": len(images),
            },
        )
        gateway = copy.copy(self.inner)
        if hasattr(gateway, "timeout"):
            gateway.timeout = min(
                gateway.timeout, max(0.01, self.state["deadline"] - time.time())
            )
        try:
            reply = gateway.complete(role, request, images, schema)
        except (ValueError, TypeError, OSError, RuntimeError):
            self.state["usage_complete"] = False
            self.sessions.save(self.state["run_id"], self.principal, self.state)
            self.sessions.event(self.state["run_id"], "model.failed", {"role": role})
            raise
        if role == "write":
            self.state["candidate_draft"] = reply.payload
        elif role == "verify":
            self.state["verification_observation"] = reply.payload
        if reply.usage_available:
            self.state["reported_tokens"] += (
                reply.cost.input_tokens + reply.cost.output_tokens
            )
        else:
            self.state["usage_complete"] = False
        self.sessions.save(self.state["run_id"], self.principal, self.state)
        self.sessions.event(
            self.state["run_id"],
            "model.completed",
            {
                "role": role,
                "usage": reply.cost.model_dump(mode="json")
                if reply.usage_available
                else None,
            },
        )
        self.guard()
        return reply
