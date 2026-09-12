import json

import pytest

from shopguide.models.providers import (
    WorkbuddyDeepSeekGateway,
    gateway_from_config,
)
from shopguide.models.responses import ResponsesGateway


def test_named_local_section_is_selected_without_mixing_credentials(tmp_path):
    p = tmp_path / "private.config"
    p.write_text(
        "#DeepSeek\nhttps://api.deepseek.com\nsk-oldfixture\nmodel使用deepseek-v4-flash-vision-exp\n\n# workbuddy deepseek\nhttps://api.deepseek.com\nBase URL：http://127.0.0.1:8317/v1\n模型名：deepseek-v4-flash\nsk-cpa-fixture_key\n"
    )
    g = gateway_from_config(p)
    assert isinstance(g, WorkbuddyDeepSeekGateway)
    assert g.model_id == "deepseek-v4-flash" and g.supports_images
    assert g._url == "http://127.0.0.1:8317/v1/chat/completions"
    assert g._key == "sk-cpa-fixture_key"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "http://192.168.1.1/v1",
        "https://api.deepseek.com",
        "http://user@127.0.0.1:8317/v1",
    ],
)
def test_proxy_credentials_cannot_go_to_other_hosts(url):
    with pytest.raises(ValueError):
        WorkbuddyDeepSeekGateway("deepseek-v4-flash", url, "fixture")


def test_default_gateway_still_rejects_plain_http():
    with pytest.raises(ValueError):
        ResponsesGateway("fixture", "http://127.0.0.1:8317/v1", "fixture")


def test_proxy_uses_strict_json_instructions_and_no_ambient_proxy():
    captured = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": '{"asset_ids":[]}',
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 4},
                }
            ).encode()

    class Opener:
        def open(self, req, timeout):
            captured.append(json.loads(req.data))
            return Reply()

    g = WorkbuddyDeepSeekGateway(
        "deepseek-v4-flash", "http://127.0.0.1:8317/v1", "fixture", opener=Opener()
    )
    assert g.complete(
        "select",
        {"allowed_asset_ids": []},
        {},
        {
            "type": "object",
            "properties": {"asset_ids": {"type": "array", "items": {"type": "string"}}},
        },
    ).payload == {"asset_ids": []}
    assert captured[0]["messages"][0]["role"] == "system"
    assert "JSON" in captured[0]["messages"][0]["content"]
    assert captured[0]["temperature"] == 0

    assert captured[0]["thinking"] == {"type": "disabled"}
    assert "reasoning" not in captured[0] and "reasoning_effort" not in captured[0]
    assert captured[0]["response_format"] == {"type": "json_object"}
    assert "asset_ids" in captured[0]["messages"][0]["content"]


def test_chat_length_or_reasoning_only_is_not_a_completed_answer():
    g = WorkbuddyDeepSeekGateway(
        "deepseek-v4-flash", "http://127.0.0.1:8317/v1", "fixture"
    )
    value = g.normalize_response(
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": None,
                        "reasoning_content": "PRIVATE_THOUGHT",
                    },
                }
            ]
        }
    )
    assert value["status"] == "incomplete" and "PRIVATE_THOUGHT" not in json.dumps(
        value
    )


def test_chat_unknown_usage_is_not_zero_cost():
    g = WorkbuddyDeepSeekGateway(
        "deepseek-v4-flash", "http://127.0.0.1:8317/v1", "fixture"
    )
    value = g.normalize_response(
        {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
    )
    assert value["usage"] == {}
