"""Two bounded capability probes using an explicitly supplied private local config.

Never logs credentials, endpoint URL, raw errors, or private reasoning. Not an eval.
"""

import argparse
import base64
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw


def safe_http_diagnostic(error):
    """Keep actionable classifications without logging arbitrary provider text."""
    diagnostic = {"http_status": error.code}
    request_id = error.headers.get("X-Request-Id", "") if error.headers else ""
    if re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", request_id
    ):
        diagnostic["request_id"] = request_id
    try:
        payload = json.loads(error.read(65536))
        detail = payload.get("error", payload) if isinstance(payload, dict) else {}
        if not isinstance(detail, dict):
            return diagnostic
        message = str(detail.get("message", "")).lower()
        error_type = detail.get("type")
        if error_type in (
            "upstream_error",
            "api_error",
            "invalid_request_error",
            "authentication_error",
        ):
            diagnostic["provider_error_type"] = error_type
        if "upstream access forbidden" in message:
            diagnostic["error_category"] = "upstream_access_forbidden"
        elif "service temporarily unavailable" in message:
            diagnostic["error_category"] = "service_temporarily_unavailable"
        elif detail.get("code") == "API_KEY_REQUIRED":
            diagnostic["error_category"] = "api_key_required"
    except (OSError, ValueError):
        pass
    return diagnostic


def read_private_config(path):
    raw = path.read_text()

    def value(name):
        match = re.search(r"^\s*" + name + r'\s*=\s*"([^"\n]+)"', raw, re.MULTILINE)
        if not match:
            raise ValueError("missing configuration field")
        return match.group(1)

    key = re.search(r'"OPENAI_API_KEY"\s*:\s*"([^"\n]+)"', raw)
    if not key:
        raise ValueError("missing API credential")
    if value("wire_api") != "responses":
        raise ValueError("only Responses wire format supported")
    url = value("base_url").rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("private model endpoint must use HTTPS")
    return value("model"), url + "/responses", key.group(1)


def request(model, url, key, content, schema):
    body = {
        "model": model,
        "store": False,
        "max_output_tokens": 300,
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "capability_probe",
                "strict": True,
                "schema": schema,
            }
        },
    }
    started = time.monotonic()
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(req, timeout=45) as response:
            payload = json.load(response)
        texts = [
            p["text"]
            for o in payload.get("output", [])
            if o.get("type") == "message"
            for p in o.get("content", [])
            if p.get("type") == "output_text"
        ]
        parsed = json.loads("".join(texts))
        return {
            "status": "completed",
            "model_returned": payload.get("model"),
            "parsed": parsed,
            "usage": payload.get("usage"),
            "latency_s": round(time.monotonic() - started, 3),
        }
    except urllib.error.HTTPError as error:
        return {
            "status": "BLOCKED",
            **safe_http_diagnostic(error),
            "latency_s": round(time.monotonic() - started, 3),
        }
    except Exception as error:  # noqa: BLE001 - redact transport/config details
        return {
            "status": "BLOCKED",
            "error_type": type(error).__name__,
            "latency_s": round(time.monotonic() - started, 3),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    model, url, key = read_private_config(args.private_config)
    action_schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["clarify"]},
            "question": {"type": "string"},
            "missing_fields": {"type": "array", "items": {"type": "string"}},
            "choices": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["kind", "question", "missing_fields", "choices"],
        "additionalProperties": False,
    }
    planner = request(
        model,
        url,
        key,
        [
            {
                "type": "input_text",
                "text": "A user owns TEST-100 and TEST-101 and asks how to connect it. The model is not selected. Return a clarify action asking which model.",
            }
        ],
        action_schema,
    )
    # Synthetic image carries visual information not stated in the prompt.
    im = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(im)
    draw.rectangle((20, 25, 90, 95), fill="red")
    draw.ellipse((145, 25, 215, 95), fill="blue")
    out = io.BytesIO()
    im.save(out, format="PNG")
    png = out.getvalue()
    visual_schema = {
        "type": "object",
        "properties": {
            k: {"type": "string"}
            for k in ("left_color", "left_shape", "right_color", "right_shape")
        },
        "required": ["left_color", "left_shape", "right_color", "right_shape"],
        "additionalProperties": False,
    }
    vision = request(
        model,
        url,
        key,
        [
            {
                "type": "input_text",
                "text": "Identify the color and shape of each object, left and right. Use simple lowercase English color and shape names.",
            },
            {
                "type": "input_image",
                "image_url": "data:image/png;base64," + base64.b64encode(png).decode(),
            },
        ],
        visual_schema,
    )
    if planner["status"] == "completed":
        from pydantic import TypeAdapter

        from shopguide.schemas import Action

        try:
            parsed = TypeAdapter(Action).validate_json(json.dumps(planner["parsed"]))
            planner["contract_valid"] = parsed.kind == "clarify" and bool(
                parsed.missing_fields
            )
        except (ValueError, TypeError):
            planner["contract_valid"] = False
    if vision["status"] == "completed":
        v = vision["parsed"]
        vision["visual_check_passed"] = (
            v.get("left_color") == "red"
            and v.get("left_shape") in ("square", "rectangle")
            and v.get("right_color") == "blue"
            and v.get("right_shape") == "circle"
        )
    report = {
        "profile": "user-config-capability-smoke",
        "model_requested": model,
        "model_mode": "real",
        "max_requests": 2,
        "max_output_tokens_per_request": 300,
        "timeout_seconds": 45,
        "retries": 0,
        "source_revision": None,
        "cost_currency": None,
        "cost_note": "Provider tariff not supplied; tokens recorded, no invented price.",
        "planner": planner,
        "vision": vision,
        "synthetic_image_sha256": hashlib.sha256(png).hexdigest(),
        "official_benchmark": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "planner_status": planner["status"],
                "vision_status": vision["status"],
                "contract_valid": planner.get("contract_valid"),
                "visual_check_passed": vision.get("visual_check_passed"),
            }
        )
    )

    if not planner.get("contract_valid") or not vision.get("visual_check_passed"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
