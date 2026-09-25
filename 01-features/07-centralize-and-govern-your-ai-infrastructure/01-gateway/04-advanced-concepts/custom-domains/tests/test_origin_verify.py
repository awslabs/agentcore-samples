# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
import base64
import importlib.util
import json
import os
from pathlib import Path

# The interceptor reads its expected header/value from the environment at
# import time, so set them before loading the module.
os.environ.setdefault("ORIGIN_VERIFY_HEADER", "X-AgentCore-Origin-Verify")
os.environ.setdefault("ORIGIN_VERIFY_VALUE", "s3cr3t")

_SPEC = importlib.util.spec_from_file_location(
    "origin_verify_index",
    Path(__file__).resolve().parents[1] / "lambda" / "origin_verify" / "index.py",
)
oiv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oiv)

HEADER = "X-AgentCore-Origin-Verify"
VALUE = "s3cr3t"


def _mcp_event(header_value, method="tools/list"):
    headers = {} if header_value is None else {HEADER: header_value}
    return {
        "interceptorInputVersion": "1.0",
        "mcp": {
            "gatewayRequest": {
                "path": "/mcp",
                "httpMethod": "POST",
                "headers": headers,
                "body": {"jsonrpc": "2.0", "id": 7, "method": method},
            }
        },
    }


def _http_event(header_value, path="/agent/invocations"):
    headers = {"Content-Type": "application/json"}
    if header_value is not None:
        headers[HEADER] = header_value
    body = base64.b64encode(json.dumps({"model": "auto"}).encode()).decode()
    return {
        "interceptorInputVersion": "1.0",
        "http": {
            "gatewayRequest": {
                "path": path,
                "httpMethod": "POST",
                "headers": headers,
                "body": body,
            }
        },
    }


def test_mcp_pass_through_when_header_matches():
    out = oiv.lambda_handler(_mcp_event(VALUE), None)
    assert out["interceptorOutputVersion"] == "1.0"
    assert "transformedGatewayResponse" not in out["mcp"]
    assert out["mcp"]["transformedGatewayRequest"]["body"]["method"] == "tools/list"


def test_mcp_rejects_missing_header():
    out = oiv.lambda_handler(_mcp_event(None), None)
    resp = out["mcp"]["transformedGatewayResponse"]
    assert resp["statusCode"] == 403
    assert resp["body"]["error"]["code"] == -32600
    assert resp["body"]["id"] == 7


def test_mcp_rejects_wrong_value():
    out = oiv.lambda_handler(_mcp_event("nope"), None)
    assert out["mcp"]["transformedGatewayResponse"]["statusCode"] == 403


def test_http_pass_through_is_empty_envelope():
    out = oiv.lambda_handler(_http_event(VALUE), None)
    assert out == {"interceptorOutputVersion": "1.0", "http": {}}


def test_http_rejects_missing_header_with_base64_body():
    out = oiv.lambda_handler(_http_event(None), None)
    resp = out["http"]["transformedGatewayResponse"]
    assert resp["statusCode"] == 403
    assert resp["contentType"] == "application/json"
    assert json.loads(base64.b64decode(resp["body"]))["message"] == "Forbidden"


def test_inference_uses_http_envelope():
    # Inference targets share the http payload shape.
    out = oiv.lambda_handler(_http_event(VALUE, path="/inference/v1/responses"), None)
    assert out == {"interceptorOutputVersion": "1.0", "http": {}}


def test_header_lookup_is_case_insensitive():
    event = _mcp_event(VALUE)
    # Gateway may deliver a differently-cased header key.
    event["mcp"]["gatewayRequest"]["headers"] = {"x-agentcore-origin-verify": VALUE}
    out = oiv.lambda_handler(event, None)
    assert "transformedGatewayRequest" in out["mcp"]


def test_unknown_envelope_fails_closed():
    out = oiv.lambda_handler({"interceptorInputVersion": "1.0"}, None)
    assert out["http"]["transformedGatewayResponse"]["statusCode"] == 403


def test_expected_value_fetched_from_secret_when_cache_empty(monkeypatch):
    # Production path: no plaintext env var, value is fetched by ARN once and
    # cached. Monkeypatch the fetch so the test stays offline (no boto3 call).
    calls = {"n": 0}

    def fake_expected():
        calls["n"] += 1
        return VALUE

    monkeypatch.setattr(oiv, "_expected_value", fake_expected)
    # Constant-time compare still accepts the right value and rejects a wrong one.
    assert oiv._verified({"headers": {HEADER: VALUE}}) is True
    assert oiv._verified({"headers": {HEADER: "wrong"}}) is False
    assert calls["n"] == 2


def test_wrong_value_rejected_via_constant_time_compare():
    # Same length as VALUE but different — compare_digest must still reject.
    out = oiv.lambda_handler(_mcp_event("s3cr3T"), None)
    assert out["mcp"]["transformedGatewayResponse"]["statusCode"] == 403
