"""Explicit provider routing from private local configuration; never log credentials."""

import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .responses import NoRedirect, ResponsesGateway, load_private_config


class DeepSeekGateway(ResponsesGateway):
    provider = "deepseek"

    def __init__(self, model_id, base_url, api_key, **kwargs):
        if urlsplit(base_url).hostname != "api.deepseek.com":
            raise ValueError("DeepSeek configuration requires the official endpoint")
        super().__init__(model_id, base_url, api_key, **kwargs)
        self.supports_images = model_id == "deepseek-v4-flash-vision-exp"

    def request_options(self, body):
        # DeepSeek maps developer to user; preserve instruction priority explicitly.
        body["input"][0]["role"] = "system"
        body["reasoning"] = {"effort": "none"}
        body["temperature"] = 0
        return body


class WorkbuddyDeepSeekGateway(DeepSeekGateway):
    """Explicit user-configured loopback route; independently verified image support."""

    provider = "workbuddy-deepseek"
    transport_profile = "loopback-chat-schema-instructions-v1"
    allow_loopback_http = True

    def __init__(self, model_id, base_url, api_key, **kwargs):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Workbuddy requires an explicit loopback HTTP endpoint")
        if model_id != "deepseek-v4-flash":
            raise ValueError("Workbuddy model capability has not been registered")
        if kwargs.get("opener") is None:
            kwargs["opener"] = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), NoRedirect()
            )
        ResponsesGateway.__init__(self, model_id, base_url, api_key, **kwargs)
        self.supports_images = True
        self._url = base_url.rstrip("/") + "/chat/completions"

    def request_options(self, body):
        messages = []
        for item in body["input"]:
            role = "system" if item["role"] == "developer" else item["role"]
            content = item["content"]
            if isinstance(content, list):
                parts = []
                for part in content:
                    if part["type"] == "input_text":
                        parts.append({"type": "text", "text": part["text"]})
                    elif part["type"] == "input_image":
                        parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": part["image_url"]},
                            }
                        )
                    else:
                        raise ValueError("UNSUPPORTED_PROXY_INPUT_PART")
                content = parts
            messages.append({"role": role, "content": content})
        messages[0]["content"] += (
            " Return ONLY one valid JSON object, no Markdown or extra text. Required JSON schema: "
            + json.dumps(body["text"]["format"]["schema"])
        )
        return {
            "model": body["model"],
            "messages": messages,
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": body["max_output_tokens"],
            "stream": False,
            "response_format": {"type": "json_object"},
        }

    def normalize_response(self, result):
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("choices"), list)
            or len(result["choices"]) != 1
        ):
            raise TypeError("MODEL_CHAT_OUTPUT_INVALID")
        choice = result["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise TypeError("MODEL_CHAT_OUTPUT_INVALID")
        message = choice["message"]
        content = message.get("content")
        complete = (
            choice.get("finish_reason") == "stop"
            and isinstance(content, str)
            and bool(content.strip())
        )
        raw_usage = result.get("usage")
        usage = {}
        if isinstance(raw_usage, dict) and all(
            type(raw_usage.get(k)) is int and raw_usage[k] >= 0
            for k in ("prompt_tokens", "completion_tokens")
        ):
            usage = {
                "input_tokens": raw_usage["prompt_tokens"],
                "output_tokens": raw_usage["completion_tokens"],
            }
        return {
            "status": "completed" if complete else "incomplete",
            "failure_reason": "MODEL_OUTPUT_TRUNCATED"
            if choice.get("finish_reason") == "length"
            else "MODEL_OUTPUT_EMPTY"
            if choice.get("finish_reason") == "stop"
            and not (isinstance(content, str) and content.strip())
            else "MODEL_RESPONSE_INCOMPLETE",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": content}],
                }
            ]
            if complete
            else [],
            "usage": usage,
        }


def gateway_from_config(path: Path, **kwargs):
    raw = path.read_text()
    local_sections = re.findall(
        r"(?ims)^[ \t]*#[ \t]*workbuddy[ \t]+deepseek[ \t]*\r?\n(.*?)(?=^[ \t]*#|\Z)",
        raw,
    )
    if local_sections:
        if len(local_sections) != 1:
            raise ValueError("ambiguous Workbuddy configuration")
        section = local_sections[0]
        bases = re.findall(
            r"(?mi)^[ \t]*Base[ \t]+URL[：:][ \t]*(http[^\s]+)[ \t]*$", section
        )
        local_models = re.findall(
            r"(?m)^[ \t]*模型名[：:][ \t]*([a-z0-9-]+)[ \t]*$", section
        )
        local_keys = re.findall(r"(?m)^[ \t]*(sk-cpa-[A-Za-z0-9_-]+)[ \t]*$", section)
        if len(bases) != 1 or len(local_models) != 1 or len(local_keys) != 1:
            raise ValueError("incomplete or ambiguous Workbuddy configuration")
        return WorkbuddyDeepSeekGateway(
            local_models[0], bases[0], local_keys[0], **kwargs
        )
    if "#DeepSeek" in raw or re.search(
        r"^\s*#\s*deepseek", raw, re.IGNORECASE | re.MULTILINE
    ):
        models = set(re.findall(r"deepseek-[a-z0-9-]+", raw))
        keys = re.findall(r"^\s*(sk-[A-Za-z0-9]+)\s*$", raw, re.MULTILINE)
        urls = re.findall(r"^\s*(https://\S+)\s*$", raw, re.MULTILINE)
        if len(models) != 1 or len(keys) != 1 or len(urls) != 1:
            raise ValueError("ambiguous or incomplete DeepSeek private configuration")
        return DeepSeekGateway(next(iter(models)), urls[0], keys[0], **kwargs)
    return ResponsesGateway(*load_private_config(path), **kwargs)
