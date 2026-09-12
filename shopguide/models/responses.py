"""Bounded Responses transport. No tool execution or automatic retries."""

import base64
import copy
import http.client
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from ..qa.gateway import GatewayReply
from ..qa.prompts import INSTRUCTIONS
from ..qa.verification_wire import VerificationWire, unique_json_object
from ..schemas import Cost


def load_private_config(path: Path):
    raw = path.read_text()

    def field(name):
        found = re.search(r"^\s*" + name + r'\s*=\s*"([^"\n]+)"', raw, re.MULTILINE)
        if not found:
            raise ValueError("missing private model configuration field")
        return found.group(1)

    key = re.search(r'"OPENAI_API_KEY"\s*:\s*"([^"\n]+)"', raw)
    if key is None or field("wire_api") != "responses":
        raise ValueError("invalid private Responses config")
    return field("model"), field("base_url"), key.group(1)


def strict_schema(schema):
    schema = copy.deepcopy(schema)

    def visit(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ResponsesGateway:
    transport_profile = "responses-json-schema-v1"
    allow_loopback_http = False
    verification_wire = "keyed-slots-v2"
    model_mode = "real"
    supports_images = True
    provider = "responses"

    def __init__(
        self, model_id: str, base_url: str, api_key: str, *, opener=None, timeout=25
    ):
        parsed = urlsplit(base_url)
        transport_allowed = parsed.scheme == "https" or (
            self.allow_loopback_http
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1"}
        )
        if (
            not transport_allowed
            or not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid HTTPS model endpoint")
        if not api_key or not 0 < timeout <= 30:
            raise ValueError("invalid credential/timeout")
        self.model_id = model_id
        self._url = base_url.rstrip("/") + "/responses"
        self._key = api_key
        self._opener = opener or urllib.request.build_opener(NoRedirect())
        self.timeout = timeout

    def request_options(self, body):
        return body

    def normalize_response(self, result):
        return result

    def complete(self, role, request, images, schema):
        if images and not self.supports_images:
            raise ValueError("MODEL_DOES_NOT_SUPPORT_IMAGES")
        instruction = INSTRUCTIONS.get(role)
        if role in {"specialist_guide", "specialist_troubleshoot", "specialist_policy"}:
            from ..agent.specialists import instruction as specialist_instruction

            instruction = specialist_instruction(role.removeprefix("specialist_"))
        if role == "intent":
            from ..intent.service import INSTRUCTION

            instruction = INSTRUCTION
        if instruction is None:
            from ..agent.prompts import AGENT_INSTRUCTIONS

            instruction = AGENT_INSTRUCTIONS.get(role)
        if instruction is None:
            raise ValueError("unknown model role")
        verification = VerificationWire(request) if role == "verify" else None
        if verification is not None:
            request, schema = verification.request, verification.schema
        content = [
            {"type": "input_text", "text": json.dumps(request, ensure_ascii=False)}
        ]
        for asset_id, raw in images.items():
            import io

            from PIL import Image

            with Image.open(io.BytesIO(raw)) as image:
                mime = Image.MIME[image.format or ""]
                image.verify()
            content.extend(
                [
                    {
                        "type": "input_text",
                        "text": (
                            "Private user photo, observation only, never a display asset or citation: "
                            if asset_id.startswith("upload_")
                            else "Authorized manual asset ID: "
                        )
                        + asset_id,
                    },
                    {
                        "type": "input_image",
                        "image_url": "data:"
                        + mime
                        + ";base64,"
                        + base64.b64encode(raw).decode(),
                    },
                ]
            )
        body = {
            "model": self.model_id,
            "store": False,
            "max_output_tokens": 2048,
            "input": [
                {"role": "developer", "content": instruction},
                {"role": "user", "content": content},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "shopguide_" + role,
                    "strict": True,
                    "schema": strict_schema(schema),
                }
            },
        }
        body = self.request_options(body)
        req = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": "Bearer " + self._key,
                "Content-Type": "application/json",
            },
        )
        started = monotonic()
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise RuntimeError("MODEL_RESPONSE_TOO_LARGE")
                result = json.loads(raw, object_pairs_hook=unique_json_object)
        except urllib.error.HTTPError as error:
            raise RuntimeError("MODEL_HTTP_" + str(error.code)) from None
        except urllib.error.URLError as error:
            reason = (
                "MODEL_TIMEOUT"
                if isinstance(error.reason, TimeoutError)
                else "MODEL_CONNECTION_ERROR"
            )
            raise RuntimeError(reason) from None
        except TimeoutError:
            raise RuntimeError("MODEL_TIMEOUT") from None
        except http.client.HTTPException:
            raise RuntimeError("MODEL_HTTP_PROTOCOL_ERROR") from None
        except OSError:
            raise RuntimeError("MODEL_CONNECTION_ERROR") from None
        except ValueError:
            raise RuntimeError("MODEL_RESPONSE_JSON_INVALID") from None
        result = self.normalize_response(result)
        if not isinstance(result, dict) or result.get("status") != "completed":
            incomplete_reason = result.get("failure_reason") if isinstance(result, dict) else None
            if incomplete_reason not in {"MODEL_OUTPUT_TRUNCATED", "MODEL_OUTPUT_EMPTY"}:
                incomplete_reason = "MODEL_RESPONSE_INCOMPLETE"
            raise RuntimeError(incomplete_reason)
        output = result.get("output")
        if not isinstance(output, list):
            raise TypeError("MODEL_OUTPUT_INVALID")
        texts = []
        for item in output:
            if not isinstance(item, dict):
                raise TypeError("MODEL_OUTPUT_INVALID")
            if item.get("type") != "message":
                continue
            parts = item.get("content")
            if not isinstance(parts, list):
                raise TypeError("MODEL_OUTPUT_INVALID")
            for part in parts:
                if not isinstance(part, dict):
                    raise TypeError("MODEL_OUTPUT_INVALID")
                if part.get("type") == "output_text":
                    if not isinstance(part.get("text"), str):
                        raise TypeError("MODEL_OUTPUT_INVALID")
                    texts.append(part["text"])
        if not "".join(texts).strip():
            raise TypeError("MODEL_OUTPUT_EMPTY")
        try:
            payload = json.loads("".join(texts), object_pairs_hook=unique_json_object)
        except ValueError:
            raise TypeError("MODEL_OUTPUT_INVALID_JSON") from None
        if not isinstance(payload, dict):
            raise TypeError("MODEL_OUTPUT_INVALID")
        if verification is not None:
            payload = verification.decode(payload)
        usage = result.get("usage") or {}
        if not isinstance(usage, dict):
            raise TypeError("MODEL_USAGE_INVALID")
        return GatewayReply(
            payload,
            Cost(
                latency_ms=(monotonic() - started) * 1000,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                image_inputs=len(images),
            ),
            usage_available=bool(usage),
        )
