import json

import pytest

from shopguide.models.providers import DeepSeekGateway, gateway_from_config
from shopguide.qa.fixed import FixedRAG
from shopguide.retrieval.models import RRFReranker


def test_private_plain_config_is_routed(tmp_path):
    path = tmp_path / "LLM.config"
    path.write_text(
        "#DeepSeek\n\nsk-fixture000\n\nmodel使用deepseek-v4-flash\n\nhttps://api.deepseek.com\n"
    )
    gateway = gateway_from_config(path)
    assert gateway.model_id == "deepseek-v4-flash"
    assert gateway.provider == "deepseek" and not gateway.supports_images
    assert gateway._url == "https://api.deepseek.com/responses"


def test_deepseek_system_priority_and_thinking_off():
    captured = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return json.dumps(
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"ok":true}'}],
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                }
            ).encode()

    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            return Reply()

    gateway = DeepSeekGateway(
        "deepseek-v4-flash", "https://api.deepseek.com", "fixture-key", opener=Opener()
    )
    reply = gateway.complete(
        "write",
        {"question": "fixture"},
        {},
        {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )
    body = json.loads(captured[0].data)
    assert body["model"] == "deepseek-v4-flash"
    assert body["input"][0]["role"] == "system"
    assert body["input"][1]["role"] == "user"
    assert body["reasoning"] == {"effort": "none"} and "tools" not in body
    assert reply.payload == {"ok": True}
    with pytest.raises(ValueError, match="DOES_NOT_SUPPORT_IMAGES"):
        gateway.complete("select", {}, {"asset_one": b"not sent"}, {})
    assert len(captured) == 1


def test_text_only_model_rejected_before_b2_initialization():
    gateway = DeepSeekGateway(
        "deepseek-v4-flash", "https://api.deepseek.com", "fixture-key"
    )
    with pytest.raises(ValueError, match="vision-capable"):
        FixedRAG(None, RRFReranker(), gateway, baseline="B2")


def test_explicit_vision_config_enables_images(tmp_path):
    path = tmp_path / "LLM.config"
    path.write_text(
        "#DeepSeek\nsk-fixture000\nmodel使用deepseek-v4-flash-vision-exp\nhttps://api.deepseek.com\n"
    )
    gateway = gateway_from_config(path)
    assert (
        gateway.model_id == "deepseek-v4-flash-vision-exp" and gateway.supports_images
    )
