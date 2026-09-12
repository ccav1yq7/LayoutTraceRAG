import pytest
from pydantic import ValidationError

from shopguide.evidence.renderer import render_answer
from shopguide.models import FakeVisionObserver, ScriptedPolicy
from shopguide.schemas import (
    AbstainAction,
    Citation,
    Claim,
    Contract,
    Evidence,
    GuideAnswer,
    GuideStep,
    ToolAction,
    Verification,
)
from shopguide.tools.registry import ToolDefinition, ToolRegistry


class Query(Contract):
    query: str


def test_registry_validation_and_errors(catalog):
    _, _, scope, _, _ = catalog
    calls = []
    registry = ToolRegistry()
    registry.register(
        "find_manual",
        ToolDefinition(
            Query, lambda args, ctx: calls.append(args.query) or {"text": "ok"}
        ),
    )
    action = ToolAction(
        tool_name="find_manual",
        arguments={"query": "button"},
        public_reason="Locate button",
    )
    assert registry.execute(action, None, "call_one").error.code == "SCOPE_REQUIRED"
    bad = action.model_copy(
        update={"arguments": {"query": "x", "principal_id": "user_bob"}}
    )
    assert registry.execute(bad, scope, "call_one").error.code == "INVALID_ARGUMENTS"
    assert calls == []
    assert registry.execute(action, scope, "call_one").status == "ok"
    assert calls == ["button"]
    registry.register(
        "write_ticket",
        ToolDefinition(Query, lambda a, s: calls.append("write"), read_only=False),
    )
    assert (
        registry.execute(
            action.model_copy(update={"tool_name": "write_ticket"}), scope, "call_two"
        ).error.code
        == "CONFIRMATION_REQUIRED"
    )
    assert calls == ["button"]


def test_registry_exception_redaction(catalog):
    _, _, scope, _, _ = catalog

    def fail(a, s):
        raise RuntimeError("secret-token and private/path")

    registry = ToolRegistry()
    registry.register("fail_tool", ToolDefinition(Query, fail))
    result = registry.execute(
        ToolAction(
            tool_name="fail_tool", arguments={"query": "x"}, public_reason="test"
        ),
        scope,
        "call_one",
    )
    assert result.error.code == "INTERNAL_ERROR"
    assert "secret" not in result.model_dump_json()


def test_u13_structural_renderer(catalog):
    repo, assets, scope, asset, _content = catalog
    ev = Evidence(
        evidence_id="ev_manual",
        kind="manual",
        source_locator=asset.source_locator,
        text="Synthetic fixture only",
        asset_ids=(asset.asset_id,),
        scope=scope,
        provenance="self-authored",
    )
    repo.put(ev, ev.evidence_id)
    answer = GuideAnswer(
        status="answered",
        product_ref="product_0",
        source_snapshot_id=scope.snapshot_id,
        summary="Fixture",
        claims=(
            Claim(
                claim_id="claim_one",
                kind="manual_instruction",
                text="Fixture",
                evidence_ids=(ev.evidence_id,),
            ),
        ),
        steps=(
            GuideStep(
                step_id="step_one",
                title="Inspect",
                text="Fixture",
                claim_ids=("claim_one",),
                evidence_ids=(ev.evidence_id,),
                display_asset_ids=(asset.asset_id,),
            ),
        ),
        citations=(
            Citation(evidence_id=ev.evidence_id, source_locator=ev.source_locator),
        ),
        verification=Verification(structural="pass", semantic="supported"),
    )
    result = render_answer(answer, scope, {ev.evidence_id: ev}, assets)
    assert result["steps"][0]["display_asset_ids"] == [asset.asset_id]
    assert result["verification"]["semantic"] == "not_checked"
    for text in (
        '<img src="https://invalid.test/a.png">',
        "![x](https://invalid.test/a.png)",
        "&lt;script&gt;bad&lt;/script&gt;",
    ):
        with pytest.raises(ValueError):
            render_answer(
                answer.model_copy(update={"summary": text}),
                scope,
                {ev.evidence_id: ev},
                assets,
            )
    payload = answer.model_dump(mode="json")
    payload["steps"][0]["display_asset_ids"] = ["https://invalid.test/x.png"]
    import json

    with pytest.raises(ValidationError):
        GuideAnswer.model_validate_json(json.dumps(payload))
    with pytest.raises((PermissionError, ValueError)):
        render_answer(
            answer,
            scope,
            {
                ev.evidence_id: ev.model_copy(
                    update={
                        "scope": scope.model_copy(update={"principal_id": "user_bob"})
                    }
                )
            },
            assets,
        )


def test_explicit_fake_gateways(catalog):
    _, _, _, asset, content = catalog
    policy = ScriptedPolicy([AbstainAction(reason="fixture lacks evidence")])
    assert policy.model_mode == "fake"
    assert policy.decide({}).kind == "abstain"
    with pytest.raises(RuntimeError):
        policy.decide({})
    observer = FakeVisionObserver({asset.asset_id: "Synthetic white fixture"})
    assert (
        observer.observe(asset.asset_id, content, "What is visible?").model_mode
        == "fake"
    )
