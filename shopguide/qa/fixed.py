"""Fixed retrieval -> optional image selection -> writing -> mandatory verification."""

import hashlib
import json
import re
from time import monotonic

from pydantic import TypeAdapter, ValidationError

from ..evidence.renderer import render_answer
from ..schemas import (
    ID,
    Citation,
    Claim,
    Evidence,
    GuideAnswer,
    GuideStep,
    ProductVariant,
    SearchScope,
    new_id,
)
from ..storage.repository import AssetRepository
from .context import evidence_view, source_page
from .contracts import (
    DeliveredServiceQuery,
    RAGBudget,
    RegionSelection,
    SemanticVerdict,
    WriterDraft,
)
from .prompts import INSTRUCTIONS, PROMPT_VERSION


class PipelineFailure(Exception):
    pass


class FixedRAG:
    def __init__(self, index, reranker, gateway, *, baseline="B2", budget=None):
        if baseline not in ("B1", "B2"):
            raise ValueError("only fixed B1/B2 are implemented")
        if baseline == "B2" and not getattr(gateway, "supports_images", True):
            raise ValueError(
                "B2 requires a vision-capable model; use B1 for text-only models"
            )
        self.index = index
        self.reranker = reranker
        self.gateway = gateway
        self.baseline = baseline
        self.budget = budget or RAGBudget()
        self.assets = AssetRepository(index.repository, index.asset_root)
        self.model_mode = (
            "real"
            if all(
                m == "real"
                for m in (
                    index.embedder.identity.model_mode,
                    reranker.model_mode,
                    gateway.model_mode,
                )
            )
            else "fake"
        )

    def _page(self, scope, page_id):
        # Trusted metadata inspection. Only the selected page reaches any model.
        self.index._scope(scope)
        rows = (
            self.index._table()
            .search()
            .where(self.index._filter(scope))
            .limit(None)
            .to_list()
        )
        result = []
        for row in rows:
            ev = self.index.repository.get(Evidence, row["id"])
            if ev.model_dump_json() != row["payload"]:
                raise ValueError("index/metadata mismatch")
            if source_page(ev) == page_id:
                result.append(ev)
        if not result:
            raise PipelineFailure("PAGE_NOT_IN_SCOPE")
        return sorted(
            result, key=lambda e: (e.source_locator.region_id is None, e.evidence_id)
        )

    def _retrieve(self, question, scope, profile, given_page):
        if profile not in (
            "pm209-given-page",
            "pm209-retrieved-top1",
            "pm209-multipage",
        ):
            raise ValueError("unknown retrieval profile")
        if (profile == "pm209-given-page") != (given_page is not None):
            raise ValueError("known page allowed only in given-page profile")
        if given_page is not None:
            TypeAdapter(ID).validate_python(given_page)
            selected = [given_page]
            ranked = []
        else:
            hits = self.index.search(
                question,
                scope,
                reranker=self.reranker,
                candidates=30,
                k=30,
                require_assets=False,
            )
            ranked = list(dict.fromkeys(source_page(r["evidence"]) for r in hits))
            selected = ranked[
                : 1 if profile == "pm209-retrieved-top1" else self.budget.max_pages
            ]
        evidence = []
        for page in selected:
            evidence.extend(self._page(scope, page))
        evidence = evidence[: self.budget.max_evidence]
        visited = list(dict.fromkeys(source_page(e) for e in evidence))
        return evidence, ranked, visited

    def _assemble(self, draft, scope, evidence, selected):
        if draft.status == "abstained":
            raise PipelineFailure("WRITER_ABSTAINED")
        claims = []
        steps = []
        used = []

        def claim(text, refs, kind="manual_instruction"):
            if not set(refs) <= evidence.keys():
                raise PipelineFailure("UNKNOWN_EVIDENCE")
            used.extend(refs)
            item = Claim(
                claim_id=new_id("claim"),
                kind=kind,
                text=text,
                evidence_ids=refs,
            )
            claims.append(item)
            return item.claim_id

        claim(draft.summary, draft.summary_evidence_ids)
        for prerequisite in draft.prerequisites:
            claim(prerequisite.text, prerequisite.evidence_ids)
        for step in draft.steps:
            if len(step.asset_ids) != len(set(step.asset_ids)):
                raise PipelineFailure("DUPLICATE_DISPLAY_ASSET")
            if not set(step.asset_ids) <= selected.keys():
                raise PipelineFailure("UNINSPECTED_ASSET")
            # An explicit allowed image selection is a source reference. Resolve its
            # registered owner deterministically; never select an image or use gold.
            refs = list(step.evidence_ids)
            for asset_id in step.asset_ids:
                owners = [
                    eid for eid, ev in evidence.items() if asset_id in ev.asset_ids
                ]
                if len(owners) != 1:
                    raise PipelineFailure("IMAGE_EVIDENCE_OWNER_INVALID")
                if owners[0] not in refs:
                    refs.append(owners[0])
            step_refs = tuple(refs)
            cid = claim(step.title + "\n" + step.text, step_refs)
            steps.append(
                GuideStep(
                    step_id=new_id("step"),
                    title=step.title,
                    text=step.text,
                    claim_ids=(cid,),
                    evidence_ids=step_refs,
                    display_asset_ids=step.asset_ids,
                )
            )
        # Limitations are claims too: all must pass the same independent verifier.
        # The context references locate the basis for review; they do not certify support.
        for limitation in draft.unresolved_items:
            claim(limitation, tuple(evidence), kind="inference")
        return GuideAnswer(
            status="partial" if draft.unresolved_items else draft.status,
            product_ref=scope.authorized_product_ids[0],
            source_snapshot_id=scope.snapshot_id,
            summary=draft.summary,
            prerequisites=tuple(p.text for p in draft.prerequisites),
            unresolved_items=draft.unresolved_items,
            steps=tuple(steps),
            claims=tuple(claims),
            citations=tuple(
                Citation(evidence_id=eid, source_locator=evidence[eid].source_locator)
                for eid in dict.fromkeys(used)
            ),
        )

    def run(
        self,
        question: str,
        scope: SearchScope,
        *,
        profile="pm209-multipage",
        given_page=None,
        run_id=None,
        prepared_context=None,
        prepared_asset_ids=(),
        conversation_context=None,
        supplementary_images=None,
        delivered_service_query=None,
        missing_requested_image=False,
        require_manual_image=False,
        media_issues=(),
        original_customer_question=None,
    ):
        started = monotonic()
        run_id = run_id or new_id("run")
        events = []
        stage = "prepare"
        failure_detail: dict | None = None
        image_reference_bindings = []
        verification_coverage = None
        issues = [
            v
            for v in media_issues
            if v in {"IMAGE_UNAVAILABLE", "REQUESTED_IMAGE_UNAVAILABLE"}
        ]
        ranked = []
        visited = []
        calls = 0
        image_inputs = 0
        total_input_tokens = 0
        total_output_tokens = 0

        def call(role, request, images, schema):
            nonlocal calls, image_inputs, total_input_tokens, total_output_tokens, stage
            stage = role
            if (
                calls >= self.budget.max_model_calls
                or image_inputs + len(images) > self.budget.max_image_inputs
            ):
                raise PipelineFailure("BUDGET_EXCEEDED")
            encoded = json.dumps(request, sort_keys=True, ensure_ascii=False)
            if len(encoded) > 48000 or monotonic() - started > 90:
                raise PipelineFailure("BUDGET_EXCEEDED")
            calls += 1
            image_inputs += len(images)
            event = {
                "role": role,
                "request_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                "asset_ids": list(images),
                "image_sha256": [
                    hashlib.sha256(b).hexdigest() for b in images.values()
                ],
            }
            events.append(event)
            before = monotonic()
            try:
                reply = self.gateway.complete(
                    role, request, images, schema.model_json_schema()
                )
                total_input_tokens += reply.cost.input_tokens
                total_output_tokens += reply.cost.output_tokens
                event["reported_cost"] = (
                    reply.cost.model_dump(mode="json")
                    if reply.usage_available
                    else None
                )
                event["usage_available"] = reply.usage_available
                parsed = schema.model_validate_json(json.dumps(reply.payload))
                event["status"] = "completed"
                if monotonic() - started > 90:
                    raise PipelineFailure("DEADLINE_EXCEEDED")
                return parsed
            finally:
                event["latency_ms"] = round((monotonic() - before) * 1000, 3)

        try:
            service_context = {}
            if delivered_service_query is not None:
                query_result = DeliveredServiceQuery.model_validate_json(
                    json.dumps(delivered_service_query)
                )
                service_context = {
                    "separately_delivered_service_query": query_result.model_dump(
                        mode="json"
                    )
                }
            scope = SearchScope.model_validate_json(scope.model_dump_json())
            if len(scope.authorized_product_ids) != 1:
                raise PipelineFailure("PRODUCT_AMBIGUOUS")
            self.index.repository.authorize(scope)
            variant = self.index.repository.get(ProductVariant, scope.confirmed_variant)
            if variant.product_id != scope.authorized_product_ids[0]:
                raise PermissionError("variant mismatch")
            if prepared_context is None:
                stage = "retrieve"
                evidence, ranked, visited = self._retrieve(
                    question, scope, profile, given_page
                )
            else:
                self.index._scope(scope)
                evidence = list(prepared_context)
                if len(evidence) > self.budget.max_evidence or len(
                    {e.evidence_id for e in evidence}
                ) != len(evidence):
                    raise PipelineFailure("INVALID_PREPARED_EVIDENCE")
                for ev in evidence:
                    registered = self.index.repository.get(Evidence, ev.evidence_id)
                    if (
                        registered != ev
                        or ev.scope.principal_id != scope.principal_id
                        or ev.scope.domain != scope.domain
                        or ev.scope.snapshot_id != scope.snapshot_id
                        or ev.scope.confirmed_variant != scope.confirmed_variant
                        or not set(ev.scope.authorized_product_ids)
                        <= set(scope.authorized_product_ids)
                        or not set(ev.scope.allowed_doc_version_ids)
                        <= set(scope.allowed_doc_version_ids)
                    ):
                        raise PermissionError("prepared evidence outside scope")
                    self.index.repository.authorize(ev.scope)
                visited = list(dict.fromkeys(source_page(e) for e in evidence))
                if len(visited) > self.budget.max_pages:
                    raise PipelineFailure("PAGE_BUDGET_EXCEEDED")
            if not evidence:
                raise PipelineFailure("NO_EVIDENCE")
            lookup = {e.evidence_id: e for e in evidence}
            views = [evidence_view(e) for e in evidence]
            selected = {}
            if self.baseline == "B2" and prepared_context is not None:
                allowed = {a for e in evidence for a in e.asset_ids}
                if (
                    len(prepared_asset_ids) > self.budget.max_display_images
                    or len(set(prepared_asset_ids)) != len(prepared_asset_ids)
                    or not set(prepared_asset_ids) <= allowed
                ):
                    raise PipelineFailure("UNINSPECTED_ASSET")
                selected = {
                    aid: self.assets.read(aid, scope) for aid in prepared_asset_ids
                }
            elif self.baseline == "B2":
                candidates: dict[str, bytes] = {}
                preferred = sorted(
                    views,
                    key=lambda e: (
                        e["region_kind"]
                        not in (
                            "Product Image",
                            "illustration",
                            "graphic",
                            "Table",
                            "page",
                        )
                    ),
                )
                for view in preferred:
                    for aid in view["asset_ids"]:
                        if len(candidates) >= self.budget.max_candidate_images:
                            break
                        try:
                            candidates[aid] = self.assets.read(aid, scope)
                        except (FileNotFoundError, KeyError):
                            issues.append("IMAGE_UNAVAILABLE")
                        except ValueError:
                            raise PipelineFailure("SOURCE_INTEGRITY_FAILED") from None
                    if len(candidates) >= self.budget.max_candidate_images:
                        break
                if candidates:
                    selection = call(
                        "select",
                        {
                            "question": question,
                            "evidence": [
                                {
                                    **v,
                                    "asset_ids": [
                                        a for a in v["asset_ids"] if a in candidates
                                    ],
                                }
                                for v in views
                            ],
                            "allowed_asset_ids": list(candidates),
                            "max_assets": self.budget.max_display_images,
                        },
                        candidates,
                        RegionSelection,
                    )
                    if len(selection.asset_ids) > self.budget.max_display_images or len(
                        set(selection.asset_ids)
                    ) != len(selection.asset_ids):
                        raise PipelineFailure("INVALID_REGION_SELECTION")
                    if not set(selection.asset_ids) <= candidates.keys():
                        raise PipelineFailure("UNKNOWN_ASSET")
                    selected = {a: candidates[a] for a in selection.asset_ids}
            supplementary = supplementary_images or {}
            if any(not uid.startswith("upload_") for uid in supplementary):
                raise PipelineFailure("INVALID_USER_IMAGE")
            if len(supplementary) + len(selected) > 4:
                raise PipelineFailure("IMAGE_BUDGET_EXCEEDED")
            # User photos are inputs only, never selectable as manual display assets.
            writer_images = {**selected, **supplementary}
            # B1 never exposes image IDs to Writer; B2 exposes only inspected choices.
            writer_views = [
                {**v, "asset_ids": [a for a in v["asset_ids"] if a in selected]}
                for v in views
            ]
            draft = call(
                "write",
                {
                    "question": question,
                    "original_customer_question": original_customer_question,
                    "baseline": self.baseline,
                    "conversation_context": conversation_context or [],
                    **service_context,
                    "missing_requested_image": bool(missing_requested_image),
                    "manual_image_requested": bool(require_manual_image),
                    "user_image_ids": list(supplementary),
                    "allowed_display_asset_ids": list(selected),
                    "display_asset_evidence": {
                        aid: [
                            v["evidence_id"]
                            for v in writer_views
                            if aid in v["asset_ids"]
                        ]
                        for aid in selected
                    },
                    "evidence": writer_views,
                },
                writer_images,
                WriterDraft,
            )
            if (
                require_manual_image
                and draft.status != "abstained"
                and not any(step.asset_ids for step in draft.steps)
            ):
                missing_requested_image = True
                issues.append("REQUESTED_IMAGE_UNAVAILABLE")
                draft = draft.model_copy(update={"status": "partial"})
            stage = "assemble"
            answer = self._assemble(draft, scope, lookup, selected)
            image_reference_bindings = [
                {
                    "step_id": step.step_id,
                    "asset_ids": list(step.display_asset_ids),
                    "added_evidence_ids": [
                        eid
                        for eid in step.evidence_ids
                        if eid not in original.evidence_ids
                    ],
                    "basis": "explicit selected image -> unique registered owner in current evidence",
                }
                for original, step in zip(draft.steps, answer.steps, strict=True)
                if set(step.evidence_ids) - set(original.evidence_ids)
            ]
            stage = "render"
            rendered = render_answer(
                answer, scope, lookup, self.assets
            )  # L1 and L2 are mandatory.
            used_images = {
                a: selected[a] for s in answer.steps for a in s.display_asset_ids
            }
            verdict = call(
                "verify",
                {
                    "question": question,
                    "original_customer_question": original_customer_question,
                    "answer": answer.model_dump(mode="json"),
                    "conversation_context": conversation_context or [],
                    **service_context,
                    "missing_requested_image": bool(missing_requested_image),
                    "manual_image_requested": bool(require_manual_image),
                    "user_image_ids": list(supplementary),
                    "required_claim_ids": [c.claim_id for c in answer.claims],
                    "required_image_checks": [
                        {"step_id": s.step_id, "asset_id": a}
                        for s in answer.steps
                        for a in s.display_asset_ids
                    ],
                    "evidence": views,
                },
                {**used_images, **supplementary},
                SemanticVerdict,
            )
            stage = "verify_coverage"
            expected_claims = {c.claim_id for c in answer.claims}
            expected_images = {
                (s.step_id, a) for s in answer.steps for a in s.display_asset_ids
            }
            verification_coverage = {
                "model_complete": verdict.complete,
                "incomplete_reason": verdict.incomplete_reason,
                "expected_claims": len(expected_claims),
                "returned_claims": len(verdict.claims),
                "claim_ids_match": len(verdict.claims) == len(expected_claims)
                and {c.claim_id for c in verdict.claims} == expected_claims,
                "expected_image_pairs": len(expected_images),
                "returned_image_pairs": len(verdict.images),
                "image_pairs_match": len(verdict.images) == len(expected_images)
                and {(i.step_id, i.asset_id) for i in verdict.images}
                == expected_images,
            }
            if (
                not verdict.complete
                or len(verdict.claims) != len(expected_claims)
                or {c.claim_id for c in verdict.claims} != expected_claims
                or len(verdict.images) != len(expected_images)
                or {(i.step_id, i.asset_id) for i in verdict.images} != expected_images
            ):
                raise PipelineFailure("VERIFIER_COVERAGE_INVALID")
            stage = "verify_semantics"
            if any(not c.supported for c in verdict.claims) or any(
                not i.supported for i in verdict.images
            ):
                raise PipelineFailure("UNSUPPORTED_ANSWER")
            stage = "deliver"
            # Reauthorize after model calls: revoked docs/assets cannot pass via cached context.
            rendered = render_answer(answer, scope, lookup, self.assets)
            if self.gateway.model_mode == "real":
                rendered["verification"]["semantic"] = "supported"
            # Content completeness is decided by Writer and independently checked.
            # Optional images do not determine whether the user's question was answered.
            if missing_requested_image:
                rendered["status"] = "partial"
                rendered["unresolved_items"] = list(
                    rendered.get("unresolved_items", [])
                ) + ["暂时无法展示您请求的原图；以下仅提供已核实的文字说明。"]
            elif issues:
                rendered["unresolved_items"] = list(
                    rendered.get("unresolved_items", [])
                ) + ["部分参考图片暂时不可用。"]
            rendered["verification"]["warnings"] = sorted(set(issues))
            status = "completed"
            error = None
        except (
            ValueError,
            TypeError,
            OSError,
            KeyError,
            PermissionError,
            FileNotFoundError,
            PipelineFailure,
            TimeoutError,
            RuntimeError,
        ) as failure:
            code = (
                str(failure)
                if isinstance(failure, PipelineFailure)
                else "VALIDATION_OR_DEPENDENCY_FAILED"
            )
            reasons = {
                "VERIFIER_WIRE_FIELDS_INVALID": "VERIFIER_WIRE_FIELDS_INVALID",
                "VERIFIER_WIRE_SLOTS_INVALID": "VERIFIER_WIRE_SLOTS_INVALID",
                "VERIFIER_WIRE_JUDGMENT_INVALID": "VERIFIER_WIRE_JUDGMENT_INVALID",
                "VERIFIER_WIRE_COMPLETENESS_INVALID": "VERIFIER_WIRE_COMPLETENESS_INVALID",
                "display assets not supported by step evidence": "DISPLAY_ASSET_EVIDENCE_MISMATCH",
                "HTML/Markdown images are not accepted": "NON_PLAIN_OUTPUT",
                "evidence differs from registered record": "EVIDENCE_RECORD_MISMATCH",
            }
            failure_detail = {
                "stage": stage,
                "category": "abstention"
                if code == "WRITER_ABSTAINED"
                else "schema_validation"
                if isinstance(failure, ValidationError)
                else "contract"
                if isinstance(failure, PipelineFailure)
                else "local_validation_or_dependency",
                "exception_type": type(failure).__name__,
                "reason": reasons.get(str(failure), code),
            }
            safe_model_reasons = {
                "MODEL_TIMEOUT",
                "MODEL_CONNECTION_ERROR",
                "MODEL_HTTP_PROTOCOL_ERROR",
                "MODEL_RESPONSE_JSON_INVALID",
                "MODEL_RESPONSE_TOO_LARGE",
                "MODEL_OUTPUT_TRUNCATED",
                "MODEL_OUTPUT_EMPTY",
                "MODEL_RESPONSE_INCOMPLETE",
                "MODEL_OUTPUT_INVALID",
                "MODEL_OUTPUT_INVALID_JSON",
                "MODEL_CHAT_OUTPUT_INVALID",
                "MODEL_USAGE_INVALID",
            }
            if str(failure) in safe_model_reasons:
                failure_detail["reason"] = str(failure)
            if isinstance(failure, RuntimeError) and re.fullmatch(
                r"MODEL_HTTP_[0-9]{3}", str(failure)
            ):
                failure_detail["reason"] = str(failure)
            if stage == "verify_coverage" and verification_coverage is not None:
                failure_detail["verification_coverage"] = verification_coverage
                if (
                    verification_coverage["claim_ids_match"]
                    and verification_coverage["image_pairs_match"]
                    and not verification_coverage["model_complete"]
                ):
                    failure_detail["reason"] = "VERIFIER_DECLARED_INCOMPLETE"
            if isinstance(failure, ValidationError):
                # Record schema paths/types, never model values, messages or exception context.
                fields = {
                    "status",
                    "summary",
                    "summary_evidence_ids",
                    "prerequisites",
                    "steps",
                    "unresolved_items",
                    "text",
                    "title",
                    "evidence_ids",
                    "asset_ids",
                    "claims",
                    "images",
                    "claim_id",
                    "step_id",
                    "asset_id",
                    "supported",
                    "explanation",
                    "complete",
                    "incomplete_reason",
                }
                failure_detail["validation_errors"] = [
                    {
                        "loc": [
                            part
                            if isinstance(part, int) or part in fields
                            else "[unknown-field]"
                            for part in item["loc"]
                        ],
                        "type": item["type"],
                    }
                    for item in failure.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                ]
            if code in {"WRITER_ABSTAINED", "NO_EVIDENCE"}:
                customer_summary = "现有资料还不足以确认这个问题的处理方法。"
                customer_next = "请补充您遇到的具体情况、提示信息或相关照片，我会结合当前商品资料继续核对。"
            elif failure_detail["reason"].startswith("MODEL_"):
                customer_summary = "这次暂时未能完成答复，请稍后再试。"
                customer_next = None
            else:
                customer_summary = (
                    "这次还无法确认操作建议是否可靠，暂时不能给出具体步骤。"
                )
                customer_next = (
                    "您可以补充当前操作步骤和遇到的现象；如需尽快处理，请联系商品售后。"
                )
            rendered = {
                "schema_version": 2,
                "status": "abstained",
                "summary": customer_summary,
                "followup_question": customer_next,
                "steps": [],
                "claims": [],
                "citations": [],
                "unresolved_items": [],
                "verification": {
                    "structural": "fail",
                    "semantic": "not_checked",
                    "warnings": [],
                },
            }
            status = "error"
            error = code
            for event in events:
                if "status" not in event:
                    event["status"] = "error"
        configuration = self.configuration(profile)
        return {
            "configuration": configuration,
            "configuration_sha256": hashlib.sha256(
                json.dumps(configuration, sort_keys=True).encode()
            ).hexdigest(),
            "run_id": run_id,
            "status": status,
            "error": error,
            "failure_detail": failure_detail,
            "image_reference_bindings": image_reference_bindings,
            "baseline": self.baseline,
            "protocol": profile,
            "model_mode": self.model_mode,
            "component_modes": {
                "embedding": self.index.embedder.identity.model_mode,
                "gateway": self.gateway.model_mode,
                "reranker": self.reranker.model_mode,
            },
            "model_id": self.gateway.model_id,
            "retrieved_page_ids": ranked,
            "visited_page_ids": visited,
            "given_information": {"manual": True, "page": given_page is not None},
            "answer": rendered,
            "events": events,
            "usage": {
                "model_calls": calls,
                "image_inputs": image_inputs,
                "reported_input_tokens": total_input_tokens
                if all(e.get("usage_available", False) for e in events)
                else None,
                "reported_output_tokens": total_output_tokens
                if all(e.get("usage_available", False) for e in events)
                else None,
                "elapsed_ms": round((monotonic() - started) * 1000, 3),
            },
            "official_benchmark": False,
        }

    def configuration(self, profile):
        return {
            "baseline": self.baseline,
            "protocol": profile,
            "image_reference_policy": "explicit-image-registered-owner-v1",
            "verification_wire": getattr(
                self.gateway, "verification_wire", "canonical-arrays-v1"
            ),
            "budget": self.budget.model_dump(mode="json"),
            "embedding": self.index.embedder.identity.model_dump(mode="json"),
            "model_id": self.gateway.model_id,
            "model_revision": None,
            "provider": getattr(self.gateway, "provider", "fixture"),
            "transport_profile": getattr(
                self.gateway, "transport_profile", "canonical-gateway-v1"
            ),
            "supports_images": getattr(self.gateway, "supports_images", True),
            "reranker": {
                "model_id": getattr(
                    self.reranker, "model_id", type(self.reranker).__name__
                ),
                "revision": getattr(self.reranker, "revision", None),
                "model_mode": self.reranker.model_mode,
            },
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                json.dumps(INSTRUCTIONS, sort_keys=True).encode()
            ).hexdigest(),
            "snapshot_id": self.index.snapshot,
        }
