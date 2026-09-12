"""Fault injection checks distinct, sanitized diagnostics without model requests."""

import json
import urllib.error

import pytest

from shopguide.models.providers import WorkbuddyDeepSeekGateway


@pytest.mark.parametrize(
    "failure,expected",
    [
        (TimeoutError("PRIVATE"), "MODEL_TIMEOUT"),
        (urllib.error.URLError(TimeoutError("PRIVATE")), "MODEL_TIMEOUT"),
        (urllib.error.URLError("PRIVATE"), "MODEL_CONNECTION_ERROR"),
        (urllib.error.HTTPError("PRIVATE", 503, "PRIVATE", {}, None), "MODEL_HTTP_503"),
        (b"PRIVATE_NOT_JSON", "MODEL_RESPONSE_JSON_INVALID"),
        (
            {
                "choices": [
                    {"finish_reason": "length", "message": {"content": "PRIVATE"}}
                ]
            },
            "MODEL_OUTPUT_TRUNCATED",
        ),
        (
            {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
            "MODEL_OUTPUT_EMPTY",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "PRIVATE_NOT_JSON"},
                    }
                ]
            },
            "MODEL_OUTPUT_INVALID_JSON",
        ),
    ],
)
def test_fault_classification_without_retry_or_private_output(failure, expected):
    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return (
                failure if isinstance(failure, bytes) else json.dumps(failure).encode()
            )

    class Opener:
        calls = 0

        def open(self, req, timeout):
            self.calls += 1
            if isinstance(failure, Exception):
                raise failure
            return Reply()

    opener = Opener()
    gateway = WorkbuddyDeepSeekGateway(
        "deepseek-v4-flash", "http://127.0.0.1:8317/v1", "fixture", opener=opener
    )
    with pytest.raises((RuntimeError, TypeError)) as caught:
        gateway.complete("select", {}, {}, {"type": "object", "properties": {}})
    assert str(caught.value) == expected
    assert "PRIVATE" not in str(caught.value)
    assert opener.calls == 1
