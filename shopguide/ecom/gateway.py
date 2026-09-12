"""DeepSeek native tool-call transport with a cross-process request budget."""

import copy
import fcntl
import ipaddress
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from ..models.providers import gateway_from_config
from ..models.responses import NoRedirect


def charge(path, role, tokens=None):
    with path.open("r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        state = json.load(f)
        if tokens is None:
            if state["calls"] >= state["limit"]:
                raise RuntimeError("CAMPAIGN_MODEL_BUDGET_EXHAUSTED")
            state["calls"] += 1
            state.setdefault("roles", {}).setdefault(role, 0)
            state["roles"][role] += 1
        else:
            state["reported_tokens"] = state.get("reported_tokens", 0) + tokens
        f.seek(0)
        f.truncate()
        json.dump(state, f)
        f.flush()
        return state


def attach_images(messages):
    result = copy.deepcopy(messages)
    for message in result:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        urls = list(
            dict.fromkeys(
                re.findall(
                    r'https?://[^\s<>"\x27]+?\.(?:jpg|jpeg|png|webp|gif)(?:\?[^\s<>"\x27]*)?',
                    content,
                    re.IGNORECASE,
                )
            )
        )
        if len(urls) > 8:
            raise ValueError("IMAGE_INPUT_LIMIT")
        for url in urls:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.username
                or not parsed.hostname
                or parsed.hostname == "localhost"
            ):
                raise ValueError("INVALID_IMAGE_URL")
            try:
                address = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    raise ValueError("INVALID_IMAGE_URL")
        if urls:
            message["content"] = [{"type": "text", "text": content}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in urls
            ]
    return result


class ChatGateway:
    def __init__(self, private_config, budget, role):
        self.inner = gateway_from_config(private_config)
        if self.inner.provider != "deepseek":
            raise ValueError("ECOM_REQUIRES_EXPLICIT_DEEPSEEK_PROFILE")
        self.budget = budget
        self.role = role

    def complete(self, messages, tools, *, force_final=False):
        charge(self.budget, self.role)
        body = {
            "model": self.inner.model_id,
            "messages": messages,
            "temperature": 0.3 if self.role == "user" else 0,
            "max_tokens": 2048,
            "thinking": {"type": "disabled"},
            "stream": False,
        }
        if tools:
            body.update(tools=tools, tool_choice="none" if force_final else "auto")
        request = urllib.request.Request(
            self.inner._url.removesuffix("/responses") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.inner._key,
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=30
            ) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("MODEL_RESPONSE_TOO_LARGE")
            data = json.loads(raw)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"MODEL_HTTP_{e.code}") from None
        except (OSError, ValueError):
            raise RuntimeError("MODEL_TRANSPORT_OR_FORMAT_ERROR") from None
        usage = data.get("usage", {})
        charge(self.budget, self.role, max(0, int(usage.get("total_tokens", 0))))
        message = data["choices"][0]["message"]
        return {
            k: message[k] for k in ("role", "content", "tool_calls") if k in message
        }
