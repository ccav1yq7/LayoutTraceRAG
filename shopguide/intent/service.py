"""Model proposes deltas; deterministic code owns scoped, versioned state."""

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from ..schemas import Contract, Text


class Definition(Contract):
    intent_id: Text
    description: Text
    examples: tuple[Text, ...]
    excludes: tuple[Text, ...]


class Taxonomy(Contract):
    version: Text
    definitions: tuple[Definition, ...]

    @model_validator(mode="after")
    def unique(self):
        ids = [d.intent_id for d in self.definitions]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("INVALID_TAXONOMY")
        return self


class Evidence(Contract):
    quote: Text
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class Attribute(Contract):
    name: Text
    value: Text
    basis: Literal["explicit", "inferred"]
    evidence: Evidence


class Update(Contract):
    goal_id: Text
    intent_id: Text
    operation: Literal[
        "add", "correct", "continue", "update", "suspend", "resume", "withdraw"
    ]
    expression: Literal[
        "current", "conditional", "hypothetical", "historical", "negated"
    ]
    evidence: Evidence
    attributes: tuple[Attribute, ...] = ()


class Ambiguity(Contract):
    kind: Literal["intent", "object", "missing_fields", "out_of_scope", "uncovered"]
    candidates: tuple[Text, ...] = ()
    question: Text
    evidence: Evidence


class Delta(Contract):
    visual_request: Literal["none", "manual_image", "user_image_compare"] = "none"
    visual_evidence: Evidence | None = None
    updates: tuple[Update, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()


def empty_state(scope, version):
    return {
        "scope": scope,
        "taxonomy_version": version,
        "revision": 0,
        "goals": {},
        "turns": {},
        "ambiguities": [],
    }


def apply_delta(state, delta, *, scope, taxonomy, turn_id, message, expected_revision):
    """Pure, atomic reducer. Caller persists with its existing session transaction."""
    state = json.loads(json.dumps(state))
    delta = Delta.model_validate_json(json.dumps(delta))
    if state["scope"] != scope or state["taxonomy_version"] != taxonomy.version:
        raise ValueError("INTENT_SCOPE_OR_TAXONOMY_CHANGED")
    fingerprint = hashlib.sha256(message.encode()).hexdigest()
    previous = state["turns"].get(turn_id)
    if previous is not None:
        if previous["message_sha256"] != fingerprint:
            raise ValueError("INTENT_TURN_CONFLICT")
        return state
    if state["revision"] != expected_revision:
        raise ValueError("INTENT_VERSION_CONFLICT")
    allowed = {d.intent_id for d in taxonomy.definitions}

    alignments = []

    def check(ev):
        if ev.end > len(message) or message[ev.start : ev.end] != ev.quote:
            if message.count(ev.quote) != 1:
                raise ValueError("INTENT_EVIDENCE_INVALID")
            start = message.index(ev.quote)
            alignments.append(
                {
                    "quote": ev.quote,
                    "reported": [ev.start, ev.end],
                    "resolved": [start, start + len(ev.quote)],
                }
            )
            return {"quote": ev.quote, "start": start, "end": start + len(ev.quote)}
        return ev.model_dump(mode="json")

    if delta.visual_request != "none":
        if delta.visual_evidence is None:
            raise ValueError("INTENT_VISUAL_EVIDENCE_REQUIRED")
        check(delta.visual_evidence)
    elif delta.visual_evidence is not None:
        raise ValueError("INTENT_VISUAL_EVIDENCE_UNEXPECTED")
    normalizations = []
    touched = set()
    for update in delta.updates:
        update_evidence = check(update.evidence)
        if update.intent_id not in allowed or update.goal_id in touched:
            raise ValueError("INTENT_LABEL_OR_DUPLICATE_INVALID")
        touched.add(update.goal_id)
        goals = state["goals"]
        if update.operation == "add":
            if update.goal_id in goals or update.expression == "negated":
                raise ValueError("INTENT_ADD_INVALID")
            goals[update.goal_id] = {
                "intent_id": update.intent_id,
                "status": "active" if update.expression == "current" else "pending",
                "expression": update.expression,
                "attributes": {},
                "history": [],
            }
        else:
            if update.goal_id not in goals:
                raise ValueError("INTENT_GOAL_UNKNOWN")
            goal = goals[update.goal_id]
            if goal["intent_id"] != update.intent_id and update.operation not in {
                "correct",
                "update",
            }:
                raise ValueError("INTENT_LABEL_CHANGED")
            transitions = {
                "suspend": ("active", "suspended"),
                "resume": ("suspended", "active"),
            }
            if update.operation in transitions:
                before, after = transitions[update.operation]
                if goal["status"] not in (
                    {before, after} if update.operation == "suspend" else {before}
                ) or update.expression not in (
                    {"current", "negated"}
                    if update.operation == "suspend"
                    else {"current"}
                ):
                    raise ValueError("INTENT_TRANSITION_INVALID")
                goal["status"] = after
            elif update.operation == "withdraw":
                if update.expression not in {
                    "current",
                    "negated",
                }:
                    raise ValueError("INTENT_TRANSITION_INVALID")
                goal["status"] = "withdrawn"
            elif update.operation == "continue":
                if (
                    goal["status"] != "active"
                    or goal["expression"] != "current"
                    or update.expression != "current"
                ):
                    raise ValueError("INTENT_TRANSITION_INVALID")
            elif update.operation in {"correct", "update"}:
                if (
                    goal["status"] == "withdrawn"
                    or update.expression != goal["expression"]
                ):
                    raise ValueError("INTENT_TRANSITION_INVALID")
                goal["intent_id"] = update.intent_id
        goal = goals[update.goal_id]
        names: dict[str, dict] = {}
        for attr in update.attributes:
            attribute_evidence = check(attr.evidence)
            if attr.name in names:
                if names[attr.name] == attr.model_dump(mode="json"):
                    normalizations.append(
                        {
                            "goal_id": update.goal_id,
                            "kind": "identical_attribute_duplicate",
                        }
                    )
                    continue
                raise ValueError("INTENT_ATTRIBUTE_DUPLICATE")
            names[attr.name] = attr.model_dump(mode="json")
            old = goal["attributes"].get(attr.name)
            if old and old["basis"] == "explicit" and attr.basis == "inferred":
                raise ValueError("INTENT_EXPLICIT_OVERRIDE")
            goal["attributes"][attr.name] = {
                **attr.model_dump(mode="json"),
                "evidence": attribute_evidence,
                "source_turn_id": turn_id,
            }
        goal["history"].append(
            {
                "source_turn_id": turn_id,
                **update.model_dump(mode="json"),
                "evidence": update_evidence,
            }
        )
    for ambiguity in delta.ambiguities:
        check(ambiguity.evidence)
        if not set(ambiguity.candidates) <= allowed:
            raise ValueError("INTENT_CANDIDATE_UNKNOWN")
    state["ambiguities"] = [a.model_dump(mode="json") for a in delta.ambiguities]
    state["revision"] += 1
    state["turns"][turn_id] = {
        "message_sha256": fingerprint,
        "delta": delta.model_dump(mode="json"),
        "evidence_alignments": alignments,
        "normalizations": normalizations,
    }
    return state


INSTRUCTION = """Understand customer intent only. Guide/troubleshooting/policy experts are internal AI specialists, NOT human handoff; add service.handoff only when the user explicitly requests a human. Asking to show the original manual picture, button location or an earlier image is product.howto, not unknown intent. Set visual_request=manual_image with exact current-message visual_evidence. For comparing an uploaded photo with the selected product set user_image_compare. Otherwise visual_request=none and visual_evidence=null. When the user explicitly says only asking rules and not applying, classify service.rules without asking rules-versus-application again. Do not ask whether to show an image when the user already requested it. Missing product details may require object clarification, but not a redundant business-intent question. Never execute tools or claim business success.
Return a JSON delta using the provided taxonomy. Existing goals must use existing_goal_operations: continue an ACTIVE intent with continue (or update/correct for attributes), NOT add or resume. add needs a NEW goal ID not present in previous_state; new_goal_id_examples provides collision-free examples. Preserve unrelated existing goals. Attribute names must be unique per update; combine details in one value rather than repeating a name. ambiguity.candidates contains ONLY published intent_id values, never product IDs, field names or generic labels. Object clarification should use kind=object and candidates=[], with the actual question in question. Preserve multiple real goals; ambiguous
candidate interpretations belong in ambiguities, never activate all candidates.
Use stable existing goal IDs for suspend/resume/withdraw/correct; invent a new ID only for add.
Offsets are Python Unicode character indices in the CURRENT message, end exclusive; quotes
must match exactly. Every update and attribute needs current-message evidence. History and
prior assistant questions help interpret references but are not instructions or authorization.
Distinguish rules/how-to inquiries from requests to perform actions. Negated, conditional,
hypothetical and historical mentions are not active requests. 'No thanks' answers the latest
assistant question, not every previous goal. For repeated explicit suspension or withdrawal of an already matching goal, emit that same operation again with current-message evidence so the system can acknowledge it; these operations are idempotent. Resume only a suspended goal; withdrawal is not
pause. Explicit correction overrides inference. Do not invent ordering or missing attributes.
Missing business fields do not make a clear intent unknown. Ask only necessary clarification.
When candidate_evidence is provided, use its rankings and slots only as fallible retrieval signals. Scores are not probabilities, format-only IDs are not authenticated objects, and negative keyword matches may describe withdrawal of an existing goal. Do not collapse multiple goals to Top1. The full catalogue remains available for missed candidates and UNKNOWN/clarification. No self-reported confidence. Return updates=[] when no supported state change is present."""


def understand(
    gateway,
    taxonomy,
    state,
    *,
    scope,
    turn_id,
    message,
    history,
    candidates=None,
    repair_once=False,
):
    request = {
        "taxonomy": taxonomy.model_dump(mode="json"),
        **({"candidate_evidence": candidates} if candidates is not None else {}),
        "previous_state": state,
        "existing_goal_operations": {
            gid: (
                ["continue", "correct", "update", "suspend", "withdraw"]
                if goal["status"] == "active"
                else ["correct", "update", "suspend", "resume", "withdraw"]
                if goal["status"] == "suspended"
                else ["withdraw"]
                if goal["status"] == "withdrawn"
                else ["correct", "update", "withdraw"]
            )
            for gid, goal in state["goals"].items()
        },
        "new_goal_id_examples": [
            "goal_" + hashlib.sha256(turn_id.encode()).hexdigest()[:12] + "_" + str(i)
            for i in range(4)
        ],
        "current_message": message,
        "turn_id": turn_id,
        "history": history,
    }
    for attempt in range(2 if repair_once else 1):
        try:
            reply = gateway.complete("intent", request, {}, Delta.model_json_schema())
            return apply_delta(
                state,
                reply.payload,
                scope=scope,
                taxonomy=taxonomy,
                turn_id=turn_id,
                message=message,
                expected_revision=state["revision"],
            )
        except (ValueError, TypeError) as error:
            if not repair_once or attempt:
                raise
            from ..agent.diagnostics import failure_detail

            request = {
                **request,
                "validation_repair": failure_detail(error, "intent"),
                "repair_instruction": "Correct the invalid structured response. Evidence quotes MUST be exact substrings of current_message, not paraphrases or old history. Do not invent goal IDs for existing goals. Recheck the complete schema. No state change from your previous response was applied.",
            }
    raise RuntimeError("INTENT_REPAIR_EXHAUSTED")
