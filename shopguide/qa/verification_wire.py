"""Exact verifier slots: models judge checks, the backend owns their identifiers."""

import copy

REASONS = ("none", "review_incomplete", "answer_incomplete", "evidence_uncertain")


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


class VerificationWire:
    def __init__(self, request):
        ids = request["required_claim_ids"]
        pairs = request["required_image_checks"]
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(i, str) or not i for i in ids)
            or len(ids) != len(set(ids))
            or not isinstance(pairs, list)
            or any(
                not isinstance(p, dict)
                or set(p) != {"step_id", "asset_id"}
                or any(not isinstance(v, str) or not v for v in p.values())
                for p in pairs
            )
        ):
            raise ValueError("VERIFIER_REQUEST_INVALID")
        if len({(p["step_id"], p["asset_id"]) for p in pairs}) != len(pairs):
            raise ValueError("VERIFIER_REQUEST_DUPLICATE_CHECK")
        if ids != [c["claim_id"] for c in request["answer"]["claims"]]:
            raise ValueError("VERIFIER_REQUEST_CLAIMS_MISMATCH")
        answer_pairs = [
            {"step_id": step["step_id"], "asset_id": aid}
            for step in request["answer"].get("steps", [])
            for aid in step["display_asset_ids"]
        ]
        if pairs != answer_pairs:
            raise ValueError("VERIFIER_REQUEST_IMAGES_MISMATCH")
        self.claims = {f"c{i}": identifier for i, identifier in enumerate(ids)}
        self.images = {f"i{i}": dict(pair) for i, pair in enumerate(pairs)}
        self.request = copy.deepcopy(request)
        self.request["verification_slots"] = {
            "claims": self.claims,
            "images": self.images,
        }
        judgment = object_schema(
            {
                "supported": {"type": "boolean"},
                "explanation": {"type": "string", "minLength": 1},
            }
        )
        self.schema = object_schema(
            {
                "claims": object_schema(
                    {key: copy.deepcopy(judgment) for key in self.claims}
                ),
                "images": object_schema(
                    {key: copy.deepcopy(judgment) for key in self.images}
                ),
                "complete": {"type": "boolean"},
                "incomplete_reason": {"type": "string", "enum": list(REASONS)},
            }
        )

        if not self.images:
            # The provider rejects object schemas with zero properties. Omit this
            # wire field entirely; canonical SemanticVerdict still receives [].
            del self.schema["properties"]["images"]
            self.schema["required"].remove("images")

    def decode(self, payload):
        expected_fields = {"claims", "complete", "incomplete_reason"}
        if self.images:
            expected_fields.add("images")
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise ValueError("VERIFIER_WIRE_FIELDS_INVALID")
        for name, expected in (("claims", self.claims), ("images", self.images)):
            if name == "images" and not expected:
                continue
            values = payload[name]
            if not isinstance(values, dict) or set(values) != set(expected):
                raise ValueError("VERIFIER_WIRE_SLOTS_INVALID")
            for value in values.values():
                if (
                    not isinstance(value, dict)
                    or set(value) != {"supported", "explanation"}
                    or type(value["supported"]) is not bool
                    or not isinstance(value["explanation"], str)
                    or not value["explanation"].strip()
                ):
                    raise ValueError("VERIFIER_WIRE_JUDGMENT_INVALID")
        complete, reason = payload["complete"], payload["incomplete_reason"]
        if (
            type(complete) is not bool
            or reason not in REASONS
            or complete != (reason == "none")
        ):
            raise ValueError("VERIFIER_WIRE_COMPLETENESS_INVALID")
        return {
            "claims": [
                {"claim_id": identifier, **payload["claims"][key]}
                for key, identifier in self.claims.items()
            ],
            "images": [
                {**pair, **payload["images"][key]} for key, pair in self.images.items()
            ],
            "complete": complete,
            "incomplete_reason": reason,
        }
