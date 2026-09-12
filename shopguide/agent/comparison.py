"""Engineering B2 shared-tool control versus B3, under identical outer budgets."""

from ..qa.context import source_page
from ..schemas import new_id
from ..sessions.store import Sessions
from .budget import BudgetStop
from .contracts import AgentBudget
from .runtime import AgentRunner


class FixedToolRunner(AgentRunner):
    """Search -> read page -> inspect -> finalize. No model planner requests."""

    controller_name = "fixed_shared_tools"

    def _plan(self, state):
        self._bind(state)
        try:
            self.meter.guard()
            state["planner_steps"] += 1
            if state["planner_steps"] > self.budget.max_planner_steps:
                return self._save(self._fallback(state, "CONTROLLER_BUDGET"), "fixed")
            session = self.sessions.get(state["session_id"], self.principal)
            if not session["product"]:
                state["pending"] = {
                    "kind": "clarify",
                    "question": "Which product do you want to use?",
                    "missing_fields": ["product"],
                    "choices": [],
                }
            elif not state["evidence_ids"]:
                state["pending"] = {
                    "kind": "tool",
                    "tool_name": "search_manuals",
                    "arguments": {"query": state["question"], "top_k": 8},
                    "public_reason": "Fixed search",
                }
            elif not state.get("fixed_page_read"):
                ev = self.tools.validate_evidence(state["evidence_ids"][0])
                state["fixed_page_read"] = True
                state["pending"] = {
                    "kind": "tool",
                    "tool_name": "read_page",
                    "arguments": {
                        "page_id": source_page(ev),
                        "include_neighbors": False,
                    },
                    "public_reason": "Fixed page read",
                }
            elif not state["inspected_asset_ids"] and self.meter.supports_images:
                assets = [
                    a
                    for eid in state["evidence_ids"]
                    for a in self.tools.validate_evidence(eid).asset_ids
                ]
                if assets:
                    state["pending"] = {
                        "kind": "tool",
                        "tool_name": "inspect_asset",
                        "arguments": {
                            "asset_id": assets[0],
                            "question": state["question"],
                        },
                        "public_reason": "Fixed first-image inspection",
                    }
                else:
                    state["pending"] = {
                        "kind": "draft_answer",
                        "evidence_ids": state["evidence_ids"][:32],
                        "requested_format": "steps",
                    }
            else:
                state["pending"] = {
                    "kind": "draft_answer",
                    "evidence_ids": state["evidence_ids"][:32],
                    "requested_format": "steps",
                }
        except BudgetStop as error:
            self._halt(state, str(error))
        return self._save(state, "fixed")


def compare(
    repository,
    root,
    index_factory,
    reranker,
    gateway_factory,
    principal,
    product,
    variant,
    snapshot,
    question,
    *,
    budget=None,
):
    budget = budget or AgentBudget()
    sessions = Sessions(repository)
    results = {}
    for label, runner_type in [
        ("B2_shared_tools", FixedToolRunner),
        ("B3", AgentRunner),
    ]:
        session = sessions.create(principal, "demo", snapshot)
        session = sessions.select(
            session["id"], principal, product, variant, session["revision"]
        )
        run = sessions.submit(
            session["id"], principal, new_id("message"), question, session["revision"]
        )
        runner = runner_type(
            repository, root, index_factory, reranker, gateway_factory(), budget=budget
        )
        record = runner.execute(run["id"], principal)
        results[label] = {
            "run_id": run["id"],
            "status": record["status"],
            "result": record["result"],
            "events": sessions.events(run["id"], principal),
            "configuration": record["state"]["configuration"],
        }
    return {
        "kind": "engineering_shared_tools_comparison",
        "official_benchmark": False,
        "snapshot": snapshot,
        "budget": budget.model_dump(mode="json"),
        "order": ["B2_shared_tools", "B3"],
        "results": results,
        "note": "B2_shared_tools is the fixed common-tool control, not a replacement for historical M3 B2 scores. One example does not establish accuracy gains.",
    }
