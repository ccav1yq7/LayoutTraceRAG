import copy
import json

import pytest

from shopguide.qa.verification_wire import (
    VerificationWire,
    unique_json_object,
)


def request():
    return {
        "required_claim_ids": ["claim_a", "claim_b"],
        "required_image_checks": [{"step_id": "step_a", "asset_id": "asset_a"}],
        "answer": {
            "claims": [
                {"claim_id": "claim_a", "text": "first"},
                {"claim_id": "claim_b", "text": "second"},
            ],
            "steps": [{"step_id": "step_a", "display_asset_ids": ["asset_a"]}],
        },
        "question": "test",
    }


def payload():
    return {
        "claims": {
            "c0": {"supported": True, "explanation": "first checked"},
            "c1": {"supported": False, "explanation": "second unsupported"},
        },
        "images": {"i0": {"supported": True, "explanation": "image checked"}},
        "complete": True,
        "incomplete_reason": "none",
    }


def test_slots_map_back_to_the_exact_original_checks():
    r = request()
    before = copy.deepcopy(r)
    wire = VerificationWire(r)
    assert r == before
    assert set(wire.schema["properties"]["claims"]["properties"]) == {"c0", "c1"}
    assert wire.request["verification_slots"]["claims"] == {
        "c0": "claim_a",
        "c1": "claim_b",
    }
    answer = wire.decode(payload())
    assert [c["claim_id"] for c in answer["claims"]] == ["claim_a", "claim_b"]
    assert answer["claims"][1]["supported"] is False
    assert answer["images"][0]["step_id"] == "step_a"
    assert answer["images"][0]["asset_id"] == "asset_a"


@pytest.mark.parametrize(
    "attack",
    [
        "missing",
        "extra",
        "old_id",
        "array",
        "boolean_string",
        "extra_field",
        "false_without_reason",
        "true_with_reason",
    ],
)
def test_malformed_slots_are_rejected_not_repaired(attack):
    p = payload()
    if attack == "missing":
        del p["claims"]["c1"]
    if attack == "extra":
        p["claims"]["c2"] = p["claims"]["c0"]
    if attack == "old_id":
        p["claims"]["claim_a"] = p["claims"].pop("c0")
    if attack == "array":
        p["claims"] = list(p["claims"].values())
    if attack == "boolean_string":
        p["claims"]["c0"]["supported"] = "true"
    if attack == "extra_field":
        p["images"]["i0"]["claim_id"] = "claim_a"
    if attack == "false_without_reason":
        p["complete"] = False
    if attack == "true_with_reason":
        p["incomplete_reason"] = "answer_incomplete"
    with pytest.raises(ValueError):
        VerificationWire(request()).decode(p)


def test_incomplete_reason_preserves_rejection_decision():
    p = payload()
    p["complete"] = False
    p["incomplete_reason"] = "answer_incomplete"
    assert VerificationWire(request()).decode(p)["complete"] is False


def test_duplicate_json_key_is_rejected_even_if_values_agree():
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        json.loads(
            '{"claims":{"c0":true,"c0":true}}', object_pairs_hook=unique_json_object
        )


def test_backend_cannot_prepare_ambiguous_check_list():
    r = request()
    r["required_claim_ids"] = ["claim_a", "claim_a"]
    with pytest.raises(ValueError):
        VerificationWire(r)
    r = request()
    r["required_image_checks"] *= 2
    with pytest.raises(ValueError):
        VerificationWire(r)


@pytest.mark.parametrize("duplicate", [False, True])
def test_real_gateway_wire_roundtrip_and_duplicate_key_rejection(duplicate):
    from shopguide.models.responses import ResponsesGateway

    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            p = payload()
            text = json.dumps(p)
            if duplicate:
                text = text.replace(
                    '"claims": {',
                    '"claims": {"c0": {"supported": true, "explanation": "duplicate"},',
                    1,
                )
            return json.dumps(
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": text}],
                        }
                    ],
                    "usage": {"input_tokens": 4, "output_tokens": 5},
                }
            ).encode()

    class Opener:
        def open(self, req, timeout):
            captured.append(json.loads(req.data))
            return Response()

    g = ResponsesGateway(
        "fixture", "https://example.invalid", "fixture-key", opener=Opener()
    )
    if duplicate:
        with pytest.raises(TypeError, match="MODEL_OUTPUT_INVALID"):
            g.complete("verify", request(), {}, {})
    else:
        r = g.complete("verify", request(), {}, {})
        assert r.payload["claims"][1]["supported"] is False
        assert r.usage_available and r.cost.input_tokens == 4
    assert len(captured) == 1
    assert set(
        captured[0]["text"]["format"]["schema"]["properties"]["claims"]["properties"]
    ) == {"c0", "c1"}
    body = json.loads(captured[0]["input"][1]["content"][0]["text"])
    assert body["verification_slots"]["images"]["i0"]["asset_id"] == "asset_a"


def test_no_images_omits_wire_field_and_cannot_add_fake_image():
    r = request()
    r["required_image_checks"] = []
    r["answer"]["steps"] = []
    p = payload()
    del p["images"]
    assert VerificationWire(r).decode(p)["images"] == []
    p["images"] = {"i0": {"supported": True, "explanation": "invented"}}
    with pytest.raises(ValueError):
        VerificationWire(r).decode(p)


def test_canonical_contract_rejects_contradictory_completion():
    from pydantic import ValidationError

    from shopguide.qa.contracts import SemanticVerdict

    value = VerificationWire(request()).decode(payload())
    value["incomplete_reason"] = "answer_incomplete"
    with pytest.raises(ValidationError):
        SemanticVerdict.model_validate_json(json.dumps(value))


def test_text_only_wire_has_no_empty_object_schema_for_provider():
    r = request()
    r["required_image_checks"] = []
    r["answer"]["steps"] = []
    schema = VerificationWire(r).schema

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("properties"), "provider rejects empty object schemas"
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    assert "images" not in schema["properties"]
