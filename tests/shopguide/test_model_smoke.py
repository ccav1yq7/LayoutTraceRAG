"""Transport-independent checks for the private, explicitly invoked capability probe."""

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest


def test_http_diagnostic_classifies_upstream_without_leaking_body():
    module = smoke_module()
    error = urllib.error.HTTPError(
        "https://example.invalid/responses",
        502,
        "Bad Gateway",
        {"X-Request-Id": "9d6a8863-ab7a-4666-99f9-d52d9c7d7616"},
        io.BytesIO(
            json.dumps(
                {
                    "error": {
                        "type": "upstream_error",
                        "message": "Upstream access forbidden, please contact administrator; fixture-secret",
                    }
                }
            ).encode()
        ),
    )
    result = module.safe_http_diagnostic(error)
    assert result["error_category"] == "upstream_access_forbidden"
    assert result["provider_error_type"] == "upstream_error"
    assert result["request_id"] == "9d6a8863-ab7a-4666-99f9-d52d9c7d7616"
    assert "fixture-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "body", [b"<html>private server error</html>", b"[]", b'{"error":"private text"}']
)
def test_http_diagnostic_tolerates_nonstandard_errors(body):
    module = smoke_module()
    error = urllib.error.HTTPError(
        "https://example.invalid", 503, "Unavailable", {}, io.BytesIO(body)
    )
    assert module.safe_http_diagnostic(error) == {"http_status": 503}


def smoke_module():
    path = Path(__file__).resolve().parents[2] / "scripts/shopguide/model_smoke.py"
    spec = importlib.util.spec_from_file_location("shopguide_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_config_honors_exact_base(tmp_path):
    module = smoke_module()
    private = tmp_path / "private.config"
    private.write_text(
        'model = "gpt-5.6-terra"\nbase_url = "https://example.invalid/custom"\n'
        'wire_api = "responses"\n{"OPENAI_API_KEY": "fixture-only"}'
    )
    model, endpoint, _key = module.read_private_config(private)
    assert model == "gpt-5.6-terra"
    assert endpoint == "https://example.invalid/custom/responses"


def test_failed_smoke_returns_failure_without_secret(tmp_path, monkeypatch):
    module = smoke_module()
    out = tmp_path / "probe.json"
    monkeypatch.setattr(
        sys, "argv", ["probe", "--private-config", "unused", "--out", str(out)]
    )
    monkeypatch.setattr(
        module,
        "read_private_config",
        lambda p: (
            "gpt-5.6-terra",
            "https://example.invalid/responses",
            "fixture-only",
        ),
    )
    monkeypatch.setattr(
        module, "request", lambda *a: {"status": "BLOCKED", "http_status": 503}
    )
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    report = json.loads(out.read_text())
    assert report["planner"]["status"] == "BLOCKED"
    assert report["official_benchmark"] is False
    assert "fixture-only" not in out.read_text()
