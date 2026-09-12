import copy
import fcntl
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter
from sqlalchemy import text

from ..qa.context import evidence_view
from ..qa.contracts import RAGBudget
from ..qa.fixed import FixedRAG
from ..schemas import ID, Action, Product
from ..sessions.store import TERMINAL, Sessions
from ..tools.registry import arguments_hash
from .budget import BudgetStop, MeteredGateway
from .contracts import AgentBudget, AgentState, PlanDecision
from .prompts import AGENT_PROMPT_VERSION
from .tools import SCHEMAS, AgentTools


class AgentRunner:
    controller_name = "agent"

    def __init__(
        self,
        repository,
        root: Path,
        index_factory,
        reranker,
        gateway,
        *,
        budget=None,
        checkpoint_hook=None,
        intent_enabled=None,
        delegation_enabled=None,
        intent_taxonomy_path=None,
        intent_candidates_enabled=None,
        intent_candidate_rules_path=None,
        intent_classifier_path=None,
        allow_development_intent_classifier=False,
    ):
        self.sessions = Sessions(repository)
        self.root = root
        self.index_factory = index_factory
        self.reranker = reranker
        self.gateway = gateway
        self.budget = budget or AgentBudget()
        self.checkpoint_hook = checkpoint_hook
        self.intent_enabled = (
            gateway.model_mode == "real" and self.controller_name == "agent"
            if intent_enabled is None
            else intent_enabled
        )
        self.delegation_enabled = (
            gateway.model_mode == "real" and self.controller_name == "agent"
            if delegation_enabled is None
            else delegation_enabled
        )
        self.intent_candidates_enabled = (
            self.intent_enabled
            if intent_candidates_enabled is None
            else intent_candidates_enabled
        )
        self.intent_candidate_rules_path = Path(
            intent_candidate_rules_path
            or Path(__file__).parents[1] / "intent" / "candidate_rules.json"
        )
        self.intent_candidate_rules_sha256 = (
            hashlib.sha256(self.intent_candidate_rules_path.read_bytes()).hexdigest()
            if self.intent_candidates_enabled
            else None
        )
        self.intent_taxonomy = None
        if self.intent_enabled:
            from ..intent.service import Taxonomy

            path = (
                intent_taxonomy_path
                or Path(__file__).parents[1] / "intent" / "taxonomy.json"
            )
            self.intent_taxonomy = Taxonomy.model_validate_json(Path(path).read_text())

        self.intent_classifier = None
        if intent_classifier_path is not None:
            if not self.intent_candidates_enabled or self.intent_taxonomy is None:
                raise ValueError("INTENT_CLASSIFIER_REQUIRES_CANDIDATE_STAGE")
            from ..intent.bert import BertIntentClassifier

            self.intent_classifier = BertIntentClassifier(
                intent_classifier_path,
                self.intent_taxonomy,
                allow_development=allow_development_intent_classifier,
            )

        elif (
            self.intent_candidates_enabled
            and gateway.model_mode == "real"
            and self.intent_taxonomy is not None
        ):
            from ..intent.bert import load_active_classifier

            self.intent_classifier = load_active_classifier(self.intent_taxonomy)

    def execute(self, run_id, principal):
        TypeAdapter(ID).validate_python(run_id)
        locks = self.root / "run-locks"
        locks.mkdir(parents=True, exist_ok=True)
        with (locks / run_id).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("RUN_BUSY") from None
            try:
                return copy.copy(self)._execute(run_id, principal)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _execute(self, run_id, principal):
        record = self.sessions.claim(run_id, principal)
        if record["status"] in TERMINAL:
            return record
        session = self.sessions.get(record["session"], principal)
        state = record["state"]
        self.principal = principal
        self.index = self.index_factory(session["snapshot"])
        configuration = {
            "controller": self.controller_name,
            "intent_enabled": self.intent_enabled,
            "intent_candidates_enabled": self.intent_candidates_enabled,
            "intent_classifier": self.intent_classifier.identity
            if self.intent_classifier
            else None,
            "intent_candidate_rules_sha256": self.intent_candidate_rules_sha256,
            "intent_understanding_version": "intent-understanding-candidates-v6",
            "delegation_enabled": self.delegation_enabled,
            "specialist_version": "specialists-v2",
            "specialist_profiles_sha256": self._specialist_hash(),
            "intent_taxonomy_sha256": hashlib.sha256(
                self.intent_taxonomy.model_dump_json().encode()
            ).hexdigest()
            if self.intent_taxonomy is not None
            else None,
            "model_id": self.gateway.model_id,
            "provider": getattr(self.gateway, "provider", "fixture"),
            "model_mode": self.gateway.model_mode,
            "budget": self.budget.model_dump(mode="json"),
            "embedding": self.index.embedder.identity.model_dump(mode="json"),
            "snapshot": session["snapshot"],
            "prompt_version": AGENT_PROMPT_VERSION,
            "tools_sha256": hashlib.sha256(
                json.dumps(
                    {n: s.model_json_schema() for n, s in SCHEMAS.items()},
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
        }
        if (
            state.get("configuration")
            and state["configuration"] != configuration
            and not record["cancelled"]
        ):
            raise RuntimeError("RUN_CONFIG_CHANGED")
        state["configuration"] = state.get("configuration") or configuration
        state["deadline"] = min(
            state["deadline"], time.time() + self.budget.deadline_seconds
        )
        self.meter = MeteredGateway(
            self.gateway, self.sessions, principal, state, self.budget
        )
        self.tools = AgentTools(
            self.sessions,
            principal,
            state,
            self.index,
            self.reranker,
            self.meter,
            self.budget,
        )
        if self._reconcile_effect(state) or record["cancelled"]:
            if record["cancelled"]:
                state["status"] = "cancelled"
                state["result"] = {
                    **state.get("result", {}),
                    "status": "cancelled",
                    "code": "CANCELLED",
                }
            self._terminal(state)
            return self.sessions.run(run_id, principal)
        if self.intent_enabled and not session["product"]:
            state["status"] = "waiting_input"
            state["pending"] = None
            state["result"] = {
                "status": "needs_clarification",
                "question": "请先选择要咨询的商品和型号，我会据此核对适用资料。",
                "missing_fields": ["product"],
            }
            self._terminal(state)
            return self.sessions.run(run_id, principal)
        if (
            self.intent_enabled
            and not state.get("intent_ready")
            and not self._understand(state)
        ):
            self._terminal(state)
            return self.sessions.run(run_id, principal)
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(
            sqlite3.connect(self.root / "checkpoints.db", check_same_thread=False)
        ) as connection:
            graph = StateGraph(AgentState)
            graph.add_node("plan", self._plan)
            graph.add_node("tool", self._tool)
            graph.add_node("specialist", self._specialist)
            graph.add_node("answer", self._answer)
            graph.add_node("terminal", self._terminal)
            graph.add_conditional_edges(START, self._route)
            for node in ("plan", "tool", "answer", "specialist"):
                graph.add_conditional_edges(node, self._route)
            graph.add_edge("terminal", END)
            compiled = graph.compile(checkpointer=SqliteSaver(connection))
            try:
                compiled.invoke(
                    state,
                    {"configurable": {"thread_id": run_id}, "recursion_limit": 64},
                )
            except (ValueError, TypeError, KeyError, OSError, RuntimeError):
                latest = self.sessions.run(run_id, principal)
                if latest["status"] not in TERMINAL:
                    failed = latest["state"]
                    failed["status"] = "failed"
                    failed["result"] = {
                        "status": "failed",
                        "code": "AGENT_EXECUTION_FAILED",
                    }
                    self._terminal(failed)
        return self.sessions.run(run_id, principal)

    def _understand(self, state):
        from ..intent.service import Taxonomy, empty_state, understand

        taxonomy = self.intent_taxonomy or Taxonomy.model_validate_json(
            (Path(__file__).parents[1] / "intent" / "taxonomy.json").read_text()
        )
        scope = self.principal + ":" + state["session_id"] + ":" + state["task_id"]
        previous = state.get("intent_state") or empty_state(scope, taxonomy.version)
        try:
            candidate_evidence = None
            if self.intent_candidates_enabled:
                from ..intent.candidates import CandidateRetriever

                if (
                    hashlib.sha256(
                        self.intent_candidate_rules_path.read_bytes()
                    ).hexdigest()
                    != self.intent_candidate_rules_sha256
                ):
                    raise ValueError("INTENT_RULES_CHANGED")
                retriever = CandidateRetriever(
                    taxonomy,
                    self.index.embedder,
                    self.reranker,
                    self.intent_candidate_rules_path,
                    classifier=self.intent_classifier,
                )
                candidate_evidence = retriever.retrieve(state["question"], previous)
                state["intent_candidates"] = candidate_evidence
            updated = understand(
                self.meter,
                taxonomy,
                previous,
                scope=scope,
                turn_id=state["run_id"],
                message=state["question"],
                history=state["history"],
                candidates=candidate_evidence,
                repair_once=self.budget.max_model_calls - state["model_calls"] >= 5,
            )
            if any(
                u["operation"] == "add"
                and u["intent_id"] == "service.apply"
                and u["expression"] == "current"
                for u in updated["turns"][state["run_id"]]["delta"]["updates"]
            ):
                state["intent_uncertain"] = False
            state["visual_request"] = updated["turns"][state["run_id"]]["delta"].get(
                "visual_request", "none"
            )
            state["intent_state"] = updated
            state["intent_ready"] = True
            self._save(state, "intent")
            self.sessions.event(
                state["run_id"],
                "intent.updated",
                {
                    "revision": updated["revision"],
                    "taxonomy_version": taxonomy.version,
                },
            )
            from .customer_messages import control_acknowledgement

            acknowledgement = control_acknowledgement(
                updated, state["run_id"], state.get("fulfilled_business_goals", {})
            )
            if acknowledgement is not None:
                state["pending"] = None
                state["status"] = "completed"
                state["result"] = {
                    "status": "acknowledged",
                    "message": acknowledgement,
                    "business_effect": False,
                }
                self._save(state, "intent_control_acknowledged")
                return True
            if (
                updated["ambiguities"]
                and state.get("visual_request") != "user_image_compare"
            ):
                state["pending"] = {
                    "kind": "clarify",
                    "question": updated["ambiguities"][0]["question"],
                    "missing_fields": [],
                }
            return True
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
            from .diagnostics import failure_detail

            state["intent_failure"] = failure_detail(error, "intent")
            self.sessions.event(
                state["run_id"], "intent.failed", state["intent_failure"]
            )
            state["intent_ready"] = False
            state["intent_uncertain"] = True
            self._halt(state, "INTENT_UNDERSTANDING_FAILED")
            self._save(state, "intent_failed")
            return False

    def _reconcile_effect(self, state):
        pending = state.get("pending")
        if not pending or pending.get("tool_name") != "commit_service_request":
            return False
        arguments = pending["arguments"]
        fingerprint = arguments_hash(arguments)
        with self.sessions.repository.engine.connect() as c:
            row = (
                c.execute(
                    text(
                        "SELECT * FROM sg_tool_calls WHERE run=:r AND name='commit_service_request' AND arguments_hash=:h"
                    ),
                    {"r": state["run_id"], "h": fingerprint},
                )
                .mappings()
                .one_or_none()
            )
            if not row or row["state"] not in (
                "RUNNING",
                "OUTCOME_UNKNOWN",
                "SUCCEEDED",
            ):
                return False
            confirmation = c.execute(
                text(
                    "SELECT id FROM sg_confirmations WHERE id=:id AND run=:r AND principal=:p"
                ),
                {
                    "id": arguments["confirmation_id"],
                    "r": state["run_id"],
                    "p": self.principal,
                },
            ).scalar_one_or_none()
        # Reconciliation reads a completed effect, even after the request deadline/confirmation expiry.
        result = (
            self.tools.service.recover(confirmation, self.principal)
            if confirmation
            else None
        )
        if result is None:
            self.tools.ledger._save(
                row["id"],
                "OUTCOME_UNKNOWN",
                {"status": "error", "code": "OUTCOME_UNKNOWN"},
            )
            self._halt(state, "OUTCOME_UNKNOWN")
        else:
            self.tools.ledger._save(row["id"], "SUCCEEDED", result)
            state["status"] = "completed"
            state["result"] = {
                "status": "completed",
                "service_request": result,
                "reconciled": True,
            }
            state["pending"] = None
        self.sessions.event(
            state["run_id"],
            "tool.reconciled",
            {"call_id": row["id"], "resolved": result is not None},
        )
        return True

    def _bind(self, state):
        self.meter.state = state
        self.tools.state = state

    def _save(self, state, stage):
        self.sessions.save(state["run_id"], self.principal, state)
        if self.checkpoint_hook:
            self.checkpoint_hook(stage, state)
        return state

    def _route(self, state):
        if state["status"] != "running":
            return "terminal"
        if state.get("delegation"):
            return "specialist"
        pending = state.get("pending")
        if pending is None:
            return "plan"
        return {"tool": "tool", "draft_answer": "answer"}.get(
            pending["kind"], "terminal"
        )

    def _halt(self, state, code):
        state["status"] = "cancelled" if code == "CANCELLED" else "failed"
        state["result"] = {"status": state["status"], "code": code}
        return state

    def _fallback(self, state, code):
        if (
            state["evidence_ids"]
            and self.budget.max_model_calls - state["model_calls"] >= 2
        ):
            state["pending"] = {
                "kind": "draft_answer",
                "evidence_ids": state["evidence_ids"][:32],
                "requested_format": "steps",
            }
            state["repair"] = {"code": code, "forced_finalization": True}
        else:
            self._halt(state, code)
        return state

    def _view(self, state):
        session = self.sessions.get(state["session_id"], self.principal)
        product = (
            self.sessions.repository.get(Product, session["product"]).model_dump(
                mode="json"
            )
            if session["product"]
            else None
        )
        views = []
        valid = []
        for eid in state["evidence_ids"]:
            try:
                ev = self.tools.validate_evidence(eid)
            except (ValueError, KeyError, PermissionError):
                continue
            valid.append(eid)
            view = evidence_view(ev)
            view["text"] = view["text"][:600]
            views.append(view)
        state["evidence_ids"] = valid
        allowed = {a for ev in views for a in ev["asset_ids"]}
        state["inspected_asset_ids"] = [
            a for a in state["inspected_asset_ids"] if a in allowed
        ]
        from ..api.uploads import Uploads

        upload_store = Uploads(
            self.sessions.repository, self.index.asset_root.parent / "uploads"
        )
        valid_uploads = []
        for uid in state.get("available_upload_ids", []):
            try:
                upload_store.record(
                    uid, self.principal, state["session_id"], state["task_id"]
                )
            except (PermissionError, KeyError, ValueError):
                continue
            valid_uploads.append(uid)
        state["available_upload_ids"] = valid_uploads
        state["inspected_upload_ids"] = [
            u for u in state.get("inspected_upload_ids", []) if u in valid_uploads
        ]
        safe_observations = []
        for observed in state["observations"][-6:]:
            entry = json.loads(json.dumps(observed))
            data = entry.get("result", {})
            if "evidence" in data:
                data["evidence"] = [
                    e for e in data["evidence"] if e["evidence_id"] in valid
                ]
            if data.get("asset_id") and data["asset_id"] not in allowed:
                entry["result"] = {"status": "error", "code": "SOURCE_REVOKED"}
            if data.get("upload_id") and data["upload_id"] not in valid_uploads:
                entry["result"] = {"status": "error", "code": "UPLOAD_UNAVAILABLE"}
            safe_observations.append(entry)
        history = [
            {
                "question": h["question"][:2000],
                "answer_context": json.dumps(h["answer"], ensure_ascii=False)[:3500],
            }
            for h in state["history"][-2:]
        ]
        return {
            "question": state["question"],
            "intent_state": state.get("intent_state"),
            "fulfilled_business_goals": state.get("fulfilled_business_goals", {}),
            "visual_request": state.get("visual_request", "none"),
            "media_issues": state.get("media_issues", []),
            "specialists": self._specialist_profiles()
            if self.delegation_enabled
            else {},
            "specialist_reports": state.get("specialist_reports", []),
            "collected_service_query": state.get("collected_service_query"),
            "product": product,
            "purchased_order_items": [
                {
                    "order_item_id": o.order_item_id,
                    "product_id": o.product_id,
                    "variant_id": o.variant_id,
                }
                for o in self.sessions.repository.orders(self.principal)
                if product
                and o.product_id == product["product_id"]
                and o.variant_id == session["variant"]
            ],
            "history": history,
            "evidence": views[:32],
            "inspected_asset_ids": state["inspected_asset_ids"],
            "available_upload_ids": valid_uploads,
            "inspected_upload_ids": state["inspected_upload_ids"],
            "observations": safe_observations,
            "repair": state["repair"],
            "missing_information": state.get("missing_information", []),
            "contradictions": state.get("contradictions", []),
            "tools": {n: s.model_json_schema() for n, s in SCHEMAS.items()},
            "remaining": {
                "model_calls": self.budget.max_model_calls - state["model_calls"],
                "planner_steps": self.budget.max_planner_steps - state["planner_steps"],
                "reserved_finalization_calls": 2,
            },
        }

    def _plan(self, state):
        self._bind(state)
        try:
            self.meter.guard()
            session = self.sessions.get(state["session_id"], self.principal)
            if session["product"]:
                self.tools.scope()
            if (
                state["planner_steps"] >= self.budget.max_planner_steps
                or state["model_calls"] >= self.budget.max_model_calls - 2
            ):
                return self._save(
                    self._fallback(state, "PLANNER_BUDGET_EXHAUSTED"), "budget"
                )
            if (
                state.get("visual_request") == "user_image_compare"
                and session["product"]
            ):
                available = self._view(state)["available_upload_ids"]
                unseen = [
                    uid
                    for uid in available
                    if uid not in state.get("turn_inspected_upload_ids", [])
                ]
                if unseen and state["image_inputs"] < self.budget.max_image_inputs:
                    state["pending"] = {
                        "kind": "tool",
                        "tool_name": "inspect_user_image",
                        "arguments": {
                            "upload_id": unseen[0],
                            "question": state["question"],
                        },
                        "public_reason": "先观察用户上传图片，再核对商品差异",
                    }
                    return self._save(state, "visual_observation_required")
            active_goals = [
                g
                for g in (state.get("intent_state") or {}).get("goals", {}).values()
                if g["status"] == "active" and g["expression"] == "current"
            ]
            if (
                state.get("visual_request") == "manual_image"
                and state["inspected_asset_ids"]
                and state["evidence_ids"]
                and not state.get("visual_direct_attempted")
                and active_goals
                and all(g["intent_id"] == "product.howto" for g in active_goals)
            ):
                state["visual_direct_attempted"] = True
                state["pending"] = {
                    "kind": "draft_answer",
                    "evidence_ids": state["evidence_ids"][:32],
                    "requested_format": "steps",
                }
                return self._save(state, "visual_evidence_ready")
            state["planner_steps"] += 1
            reply = self.meter.complete(
                "plan", self._view(state), {}, PlanDecision.model_json_schema()
            )
            decision = PlanDecision.model_validate_json(json.dumps(reply.payload))
            if decision.kind == "delegate":
                if (
                    not self.delegation_enabled
                    or decision.specialist not in self._specialist_profiles()
                    or not decision.task
                    or not decision.task.strip()
                    or not session["product"]
                    or state.get("delegation_count", 0) >= 2
                ):
                    raise ValueError("DELEGATION_NOT_ALLOWED")
                from .specialists import GOAL_ROLES

                understanding = state.get("intent_state")
                if understanding is not None:
                    goal = understanding["goals"].get(decision.goal_id)
                    if (
                        not state.get("intent_ready")
                        or not goal
                        or goal["status"] != "active"
                        or goal["expression"] != "current"
                        or GOAL_ROLES.get(goal["intent_id"]) != decision.specialist
                    ):
                        raise ValueError("DELEGATION_GOAL_INVALID")
                fingerprint = hashlib.sha256(
                    json.dumps(
                        [
                            decision.goal_id,
                            decision.specialist,
                            " ".join(decision.task.split()).casefold(),
                        ]
                    ).encode()
                ).hexdigest()
                if any(
                    r.get("task_fingerprint") == fingerprint
                    for r in state.get("specialist_reports", [])
                ):
                    raise ValueError("DELEGATION_REPEATED")
                state["delegation_count"] = state.get("delegation_count", 0) + 1
                state["delegation"] = {
                    "specialist": decision.specialist,
                    "goal_id": decision.goal_id,
                    "task_fingerprint": fingerprint,
                    "task": decision.task,
                    "calls": 0,
                    "initial_evidence": list(state["evidence_ids"]),
                }
                state["pending"] = None
                self.sessions.event(
                    state["run_id"],
                    "specialist.delegated",
                    {
                        "specialist": decision.specialist,
                        "ordinal": state["delegation_count"],
                    },
                )
                return self._save(state, "delegated")
            action = decision.action()
            state["pending"] = action.model_dump(mode="json")
            state["missing_information"] = list(decision.missing_fields)
            self.sessions.event(
                state["run_id"],
                "planner.decided",
                {"kind": action.kind, "tool_name": getattr(action, "tool_name", None)},
            )
        except BudgetStop as failure:
            self._halt(state, str(failure))
        except (ValueError, TypeError, KeyError) as error:
            from .diagnostics import failure_detail

            self.sessions.event(
                state["run_id"], "planner.invalid", failure_detail(error, "plan")
            )
            state["parse_failures"] += 1
            state["repair"] = {"code": "MODEL_OUTPUT_INVALID"}
            if state["parse_failures"] >= 2:
                self._halt(state, "MODEL_OUTPUT_INVALID")
        except PermissionError:
            self._halt(state, "SOURCE_SCOPE_UNAVAILABLE")
        except (OSError, RuntimeError):
            self._halt(state, "MODEL_UNAVAILABLE")
        return self._save(state, "plan")

    def _specialist_hash(self):
        from .specialists import PROFILES, instruction

        return hashlib.sha256(
            json.dumps(
                {
                    name: {**profile, "instruction": instruction(name)}
                    for name, profile in PROFILES.items()
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def _specialist_profiles(self):
        from .specialists import PROFILES

        return PROFILES

    def _specialist(self, state):
        from .specialists import execute

        return execute(self, state)

    def _tool(self, state):
        self._bind(state)
        try:
            self.meter.guard()
            if state["tool_attempts"] >= self.budget.max_tool_attempts:
                return self._save(
                    self._fallback(state, "TOOL_BUDGET_EXHAUSTED"), "budget"
                )
            action: Action = TypeAdapter(Action).validate_json(
                json.dumps(state["pending"])
            )
            if action.kind != "tool":
                raise ValueError("expected tool action")
            from .tools import normalize_read_metadata

            normalized_action = normalize_read_metadata(action)
            if normalized_action.arguments != action.arguments:
                self.sessions.event(
                    state["run_id"],
                    "tool.metadata_normalized",
                    {"tool_name": action.tool_name, "fields": ["reason"]},
                )
            action = normalized_action
            state["pending"] = action.model_dump(mode="json")
            state["tool_attempts"] += 1
            if action.tool_name not in SCHEMAS:
                raise ValueError("UNKNOWN_TOOL")
            normalized = (
                SCHEMAS[action.tool_name]
                .model_validate_json(json.dumps(action.arguments))
                .model_dump(mode="json")
            )
            fingerprint = arguments_hash(
                {
                    "name": action.tool_name,
                    "args": normalized,
                    "snapshot": self.index.snapshot,
                }
            )
            self._save(state, "before_tool")
            result, executed, call_id = self.tools.execute(action)
            self.meter.guard()
            old_evidence = set(state["evidence_ids"])
            old_assets = set(
                state["inspected_asset_ids"] + state.get("inspected_upload_ids", [])
            )
            for ev in result.get("evidence", []):
                if ev["evidence_id"] not in state["evidence_ids"]:
                    state["evidence_ids"].append(ev["evidence_id"])
                if ev["page_id"] not in state["visited_page_ids"]:
                    state["visited_page_ids"].append(ev["page_id"])
            if (
                result.get("asset_id")
                and result["asset_id"] not in state["inspected_asset_ids"]
            ):
                state["inspected_asset_ids"].append(result["asset_id"])
            if result.get("upload_id") and result["upload_id"] not in state.get(
                "inspected_upload_ids", []
            ):
                state.setdefault("inspected_upload_ids", []).append(result["upload_id"])
            if result.get("upload_id") and result["upload_id"] not in state.get(
                "turn_inspected_upload_ids", []
            ):
                state.setdefault("turn_inspected_upload_ids", []).append(
                    result["upload_id"]
                )
            state["media_issues"] = list(
                dict.fromkeys(
                    state.get("media_issues", [])
                    + [
                        code
                        for code in result.get("media_issues", [])
                        if code == "IMAGE_UNAVAILABLE"
                    ]
                )
            )
            observation = json.loads(json.dumps(result))
            for ev in observation.get("evidence", []):
                ev["text"] = ev["text"][:400]
            state["observations"] = (
                state["observations"]
                + [
                    {
                        "tool": action.tool_name,
                        "arguments": normalized,
                        "result": observation,
                    }
                ]
            )[-6:]
            no_new = old_evidence == set(state["evidence_ids"]) and old_assets == set(
                state["inspected_asset_ids"] + state.get("inspected_upload_ids", [])
            )
            state["repeats"] = (
                state["repeats"] + 1
                if state["last_tool"] == fingerprint and no_new
                else 0
            )
            state["last_tool"] = fingerprint
            state["pending"] = None
            self.sessions.event(
                state["run_id"],
                "tool.completed",
                {
                    "call_id": call_id,
                    "name": action.tool_name,
                    "executed": executed,
                    "status": result["status"],
                },
            )
            if result.get("requires_confirmation"):
                state["pending"] = {
                    "kind": "tool",
                    "tool_name": "commit_service_request",
                    "arguments": {"confirmation_id": result["confirmation_id"]},
                    "public_reason": "Execute the user-approved simulated request",
                }
                state["status"] = "waiting_confirmation"
                state["result"] = {"status": "waiting_confirmation", **result}
                state["result"]["status"] = "waiting_confirmation"
            elif action.tool_name == "query_service_requests":
                state["collected_service_query"] = result
                state["collected_service_query_args"] = normalized
                goals = (state.get("intent_state") or {}).get("goals", {})
                delta = (
                    (state.get("intent_state") or {})
                    .get("turns", {})
                    .get(state["run_id"], {})
                    .get("delta", {})
                )
                updates = delta.get("updates", [])
                only_query_now = (
                    bool(updates)
                    and not delta.get("ambiguities")
                    and delta.get("visual_request", "none") == "none"
                    and any(
                        u["intent_id"] == "service.progress"
                        and u["expression"] == "current"
                        for u in updates
                    )
                    and all(
                        (
                            u["intent_id"] == "service.progress"
                            and u["expression"] == "current"
                        )
                        or u["operation"] in {"suspend", "withdraw"}
                        for u in updates
                    )
                )
                other_goals = any(
                    g["status"] == "active"
                    and g["expression"] == "current"
                    and g["intent_id"] != "service.progress"
                    and gid not in state.get("fulfilled_business_goals", {})
                    for gid, g in goals.items()
                )
                if not other_goals or only_query_now:
                    state["status"] = "completed"
                    state["result"] = {"status": "completed", "service_query": result}
                elif state["repeats"] >= 2:
                    self._fallback(state, "NO_PROGRESS")
            elif result.get("ticket_id"):
                state["status"] = "completed"
                state["result"] = {"status": "completed", "service_request": result}
            elif result.get("code") == "OUTCOME_UNKNOWN":
                self._halt(state, "OUTCOME_UNKNOWN")
            elif state["repeats"] >= 2:
                self._fallback(state, "NO_PROGRESS")
        except BudgetStop as failure:
            self._halt(state, str(failure))
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
            from .diagnostics import failure_detail

            self.sessions.event(
                state["run_id"], "tool.failed", failure_detail(error, "tool")
            )
            if isinstance(error, FileNotFoundError):
                state.setdefault("media_issues", []).append("IMAGE_UNAVAILABLE")
            state["pending"] = None
            state["observations"] = (
                state["observations"]
                + [
                    {
                        "status": "error",
                        "code": "TOOL_ARGUMENT_OR_SCOPE_ERROR",
                        "diagnostic": failure_detail(error, "tool"),
                    }
                ]
            )[-6:]
        return self._save(state, "tool")

    def _answer(self, state):
        self._bind(state)
        try:
            self.meter.guard()
            selected = state["pending"]["evidence_ids"]
            if not selected or not set(selected) <= set(state["evidence_ids"]):
                raise ValueError("UNRETRIEVED_EVIDENCE")
            evidence = [
                self.tools.validate_evidence(eid) for eid in dict.fromkeys(selected)
            ][:32]
            image_ids = [
                a
                for a in state["inspected_asset_ids"]
                if any(a in e.asset_ids for e in evidence)
            ]
            if state.get("visual_request") == "manual_image" and not image_ids:
                allowed = list(
                    dict.fromkeys(a for ev in evidence for a in ev.asset_ids)
                )
                if (
                    allowed
                    and state["model_calls"] + 3 <= self.budget.max_model_calls
                    and state["image_inputs"] + 3 <= self.budget.max_image_inputs
                ):
                    draft_action = state["pending"]
                    state["pending"] = {
                        "kind": "tool",
                        "tool_name": "inspect_asset",
                        "arguments": {
                            "asset_id": allowed[0],
                            "question": state["question"],
                        },
                        "public_reason": "核对用户请求的说明书原图",
                    }
                    self._tool(state)
                    state["pending"] = draft_action
                    if state["status"] != "running":
                        return self._save(state, "visual_stopped")
                    image_ids = [
                        a for a in state["inspected_asset_ids"] if a in allowed
                    ]
                if not image_ids:
                    state.setdefault("media_issues", []).append(
                        "REQUESTED_IMAGE_UNAVAILABLE"
                    )
            image_allowance = (
                self.budget.max_image_inputs - state["image_inputs"]
            ) // 2
            from ..api.uploads import Uploads

            uploads = Uploads(
                self.sessions.repository, self.index.asset_root.parent / "uploads"
            )
            upload_ids = (
                state.get("turn_inspected_upload_ids")
                or state.get("attachment_ids")
                or state.get("available_upload_ids", [])
            )
            upload_ids = list(dict.fromkeys(upload_ids))
            if len(upload_ids) > image_allowance:
                state["status"] = "waiting_input"
                state["result"] = {
                    "status": "needs_clarification",
                    "question": "本轮图片预算不足，请继续提问或减少图片数量。",
                    "missing_fields": ["image_selection"],
                }
                return self._save(state, "image_budget")
            user_images = {
                uid: uploads.read(
                    uid, self.principal, state["session_id"], state["task_id"]
                )
                for uid in upload_ids
            }
            images = tuple(image_ids[: min(2, image_allowance - len(user_images))])
            self.meter.finalizing = True
            finalizer = FixedRAG(
                self.index,
                self.reranker,
                self.meter,
                baseline="B2" if self.meter.supports_images else "B1",
                budget=RAGBudget(
                    max_model_calls=2,
                    max_image_inputs=min(
                        8, self.budget.max_image_inputs - state["image_inputs"]
                    ),
                ),
            )
            service_context = None
            if state.get("collected_service_query") is not None:
                from ..qa.contracts import DeliveredServiceQuery

                # Recheck current ownership/scope and use the actual tool records,
                # never a planner/specialist paraphrase or a manual-derived status.
                query_args = state.get("collected_service_query_args")
                if query_args is None:
                    raise ValueError("SERVICE_QUERY_PROVENANCE_MISSING")
                current_query = self.tools.service.query(
                    state["run_id"], self.principal, query_args.get("ticket_id")
                )
                state["collected_service_query"] = current_query
                service_context = DeliveredServiceQuery(
                    status=current_query["status"],
                    ticket_ids=tuple(t["ticket_id"] for t in current_query["tickets"]),
                    has_more=current_query["has_more"],
                ).model_dump(mode="json")
            final_question = state["question"]
            if service_context is not None:
                active = [
                    g
                    for gid, g in (state.get("intent_state") or {})
                    .get("goals", {})
                    .items()
                    if g["status"] == "active"
                    and g["expression"] == "current"
                    and g["intent_id"] != "service.progress"
                    and gid not in state.get("fulfilled_business_goals", {})
                ]
                quotes = []
                for goal in active:
                    if goal["intent_id"] not in {
                        "product.howto",
                        "product.troubleshoot",
                        "service.rules",
                    }:
                        break
                    spans = [
                        h["evidence"]["quote"]
                        for h in goal.get("history", [])
                        if h["source_turn_id"] == state["run_id"]
                        and h["evidence"]["quote"] in state["question"]
                    ]
                    if not spans:
                        break
                    quotes.append(spans[-1])
                if active and len(quotes) == len(active):
                    final_question = "；".join(quotes)
            result = finalizer.run(
                final_question,
                self.tools.scope(),
                run_id=state["run_id"],
                prepared_context=evidence,
                prepared_asset_ids=images,
                conversation_context=self._view(state)["history"],
                supplementary_images=user_images,
                delivered_service_query=service_context,
                original_customer_question=state["question"]
                if final_question != state["question"]
                else None,
                require_manual_image=state.get("visual_request") == "manual_image",
                missing_requested_image="REQUESTED_IMAGE_UNAVAILABLE"
                in state.get("media_issues", []),
                media_issues=state.get("media_issues", []),
            )
            self.meter.guard()
            state["pending"] = None
            if result["status"] == "completed":
                state["status"] = "completed"
                state["result"] = {
                    "status": "completed",
                    "answer": result["answer"],
                    "used_upload_ids": upload_ids,
                    "model_mode": result["model_mode"],
                }
            elif result["error"] == "WRITER_ABSTAINED":
                from .customer_messages import insufficient_information_message

                state["status"] = "completed"
                state["result"] = {
                    "status": "abstained",
                    "message": insufficient_information_message(state),
                    "abstention_code": "WRITER_ABSTAINED",
                }
                self.sessions.event(
                    state["run_id"], "answer.abstained", {"code": "WRITER_ABSTAINED"}
                )
            else:
                state["repair"] = {
                    "code": result["error"],
                    "failure_detail": result.get("failure_detail"),
                    "suggested_repair": "retrieve_or_inspect_or_abstain",
                }
                state["contradictions"] = [result["error"] or "VERIFICATION_FAILED"]
                self.sessions.event(
                    state["run_id"], "verification.failed", state["repair"]
                )
                if state["model_calls"] >= self.budget.max_model_calls - 2:
                    self._halt(state, "VERIFICATION_FAILED")
        except BudgetStop as failure:
            self._halt(state, str(failure))
        except (ValueError, TypeError, KeyError, PermissionError, RuntimeError):
            self._halt(state, "FINALIZATION_VALIDATION_FAILED")
        finally:
            self.meter.finalizing = False
        return self._save(state, "answer")

    def _terminal(self, state):
        self._bind(state)
        if self.sessions.run(state["run_id"], self.principal)["cancelled"]:
            self._reconcile_effect(state)
            state["status"] = "cancelled"
            state["result"] = {
                **state.get("result", {}),
                "status": "cancelled",
                "code": "CANCELLED",
            }
            state["result"].pop("answer", None)
        if state["status"] == "running":
            action = state["pending"]
            if action["kind"] == "clarify":
                state["status"] = "waiting_input"
                state["result"] = {
                    "status": "needs_clarification",
                    "question": action["question"],
                    "missing_fields": action["missing_fields"],
                }
            elif action["kind"] == "handoff":
                state["status"] = "waiting_input"
                state["result"] = {
                    "status": "handoff_offered",
                    "reason": action["reason"],
                    "business_effect": False,
                }
            else:
                from .customer_messages import insufficient_information_message

                state["status"] = "completed"
                state["result"] = {
                    "status": "abstained",
                    "message": insufficient_information_message(state),
                    "reason": action.get("reason", "Insufficient evidence"),
                }
        statuses = {
            "completed": "COMPLETED",
            "failed": "FAILED",
            "cancelled": "CANCELLED",
            "waiting_input": "WAITING_INPUT",
            "waiting_confirmation": "WAITING_CONFIRMATION",
        }
        result = state["result"]
        if state.get("collected_service_query") is not None:
            result["service_query"] = state["collected_service_query"]
        result["usage"] = {
            k: state[k]
            for k in ("planner_steps", "tool_attempts", "model_calls", "image_inputs")
        }
        result["usage"]["reported_tokens"] = (
            state["reported_tokens"] if state["usage_complete"] else None
        )
        result["model_id"] = self.gateway.model_id
        result["prompt_version"] = AGENT_PROMPT_VERSION
        result["controller"] = self.controller_name
        result["official_benchmark"] = False
        self.sessions.finish(
            state["run_id"], self.principal, state, statuses[state["status"]], result
        )
        self.sessions.event(
            state["run_id"], "run." + state["status"], {"status": result["status"]}
        )
        return state
