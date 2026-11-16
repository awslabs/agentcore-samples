"""Offline unit tests for the SpecStream AgentCore Gateway sample.

Run with:
    python3 -m unittest discover -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

SAMPLE_DIR = os.path.dirname(os.path.abspath(__file__))
if SAMPLE_DIR not in sys.path:
    sys.path.insert(0, SAMPLE_DIR)

import config
import deploy
import invoke


class OpenApiSpecTests(unittest.TestCase):
    def test_spec_defines_search_and_extract_operations(self):
        spec = config.build_openapi_spec()
        self.assertEqual(spec["openapi"], "3.0.1")
        self.assertEqual(spec["servers"], [{"url": "https://api.specstream.ai"}])

        search_op = spec["paths"]["/v1/web-search"]["get"]
        self.assertEqual(search_op["operationId"], "webSearch")
        param_names = {p["name"]: p for p in search_op["parameters"]}
        self.assertTrue(param_names["q"]["required"])
        self.assertEqual(param_names["speed"]["schema"]["enum"], ["default", "fast"])

        extract_op = spec["paths"]["/v1/extract"]["post"]
        self.assertEqual(extract_op["operationId"], "extractPages")
        schema = extract_op["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(schema["required"], ["urls"])
        self.assertEqual(schema["properties"]["format"]["enum"], ["markdown", "text", "html"])


class TargetRequestTests(unittest.TestCase):
    def test_openapi_target_uses_bearer_api_key(self):
        arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/apikeycredentialprovider/specstream-api-key"
        req = config.build_target_request(
            gateway_id="gw-123",
            mode="openapi",
            credential_provider_arn=arn,
        )
        self.assertEqual(req["gatewayIdentifier"], "gw-123")
        inline = req["targetConfiguration"]["mcp"]["openApiSchema"]["inlinePayload"]
        parsed = json.loads(inline)
        self.assertIn("/v1/web-search", parsed["paths"])
        self.assertIn("/v1/extract", parsed["paths"])

        cred = req["credentialProviderConfigurations"][0]
        self.assertEqual(cred["credentialProviderType"], "API_KEY")
        api_key_cfg = cred["credentialProvider"]["apiKeyCredentialProvider"]
        self.assertEqual(api_key_cfg["providerArn"], arn)
        self.assertEqual(api_key_cfg["credentialLocation"], "HEADER")
        self.assertEqual(api_key_cfg["credentialParameterName"], "Authorization")
        self.assertEqual(api_key_cfg["credentialPrefix"], "Bearer ")

    def test_openapi_target_requires_provider_arn(self):
        with self.assertRaises(ValueError):
            config.build_target_request(gateway_id="gw-123", mode="openapi")

    def test_mcp_passthrough_target_allows_session_and_sse_headers(self):
        req = config.build_target_request(gateway_id="gw-456", mode="mcp-passthrough")
        passthrough = req["targetConfiguration"]["http"]["passthrough"]
        self.assertEqual(passthrough["endpoint"], "https://api.specstream.ai/mcp")
        self.assertEqual(passthrough["protocolType"], "MCP")
        self.assertEqual(
            req["credentialProviderConfigurations"],
            [{"credentialProviderType": "JWT_PASSTHROUGH"}],
        )
        meta = req["metadataConfiguration"]
        self.assertIn("Mcp-Session-Id", meta["allowedRequestHeaders"])
        self.assertIn("Content-Type", meta["allowedResponseHeaders"])


class PolicyAndHeaderTests(unittest.TestCase):
    def test_gateway_policies(self):
        trust = config.build_gateway_trust_policy("123456789012", "us-east-1")
        stmt = trust["Statement"][0]
        self.assertEqual(stmt["Principal"]["Service"], "bedrock-agentcore.amazonaws.com")
        self.assertEqual(stmt["Condition"]["StringEquals"]["aws:SourceAccount"], "123456789012")

        openapi_policy = config.build_gateway_role_policy("openapi")
        actions = {a for s in openapi_policy["Statement"] for a in s["Action"]}
        self.assertIn("bedrock-agentcore:GetApiKeyCredential", actions)
        self.assertIn("secretsmanager:GetSecretValue", actions)

        passthrough_policy = config.build_gateway_role_policy("mcp-passthrough")
        passthrough_actions = {a for s in passthrough_policy["Statement"] for a in s["Action"]}
        self.assertNotIn("secretsmanager:GetSecretValue", passthrough_actions)

    def test_passthrough_headers_omit_authorization_when_key_unset(self):
        headers = config.build_passthrough_headers(api_key=None, session_id="sess-1")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Mcp-Session-Id"], "sess-1")

        authed = config.build_passthrough_headers(api_key="  ss_test_123  ")
        self.assertEqual(authed["Authorization"], "Bearer ss_test_123")


class ResponseFormattingTests(unittest.TestCase):
    def test_format_search_response(self):
        payload = {
            "data": {
                "answer": {"value": "42", "page": "https://example.com/answer"},
                "sources": [{"title": "Example", "url": "https://example.com/answer", "snippet": "The answer is 42."}],
            }
        }
        rendered = config.format_search_response(payload)
        self.assertIn("Answer: 42", rendered)
        self.assertIn("https://example.com/answer", rendered)

    def test_format_search_error(self):
        rendered = config.format_search_response(
            {"error": {"code": "insufficient_balance", "message": "Top up balance"}}
        )
        self.assertEqual(rendered, "SpecStream error (insufficient_balance): Top up balance")

    def test_format_extract_response(self):
        payload = {
            "data": {
                "results": [{"url": "https://example.com", "status": "ok", "title": "Home", "text": "Hello world"}]
            }
        }
        rendered = config.format_extract_response(payload)
        self.assertIn("[ok] Home (https://example.com)", rendered)
        self.assertIn("Hello world", rendered)

    def test_parse_sse_or_json(self):
        sse = 'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}\n\n'
        self.assertEqual(invoke.parse_sse_or_json(sse)["id"], 1)
        self.assertEqual(invoke.parse_sse_or_json('{"jsonrpc": "2.0", "id": 2}')["id"], 2)


class DryRunCliTests(unittest.TestCase):
    def test_deploy_dry_run_works_without_specstream_key(self):
        for mode in ("openapi", "mcp-passthrough"):
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(buf):
                rc = deploy.deploy(["--mode", mode, "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertIn(f"Dry run (mode={mode}):", buf.getvalue())

    def test_deploy_openapi_without_key_exits_cleanly(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(buf):
            rc = deploy.deploy(["--mode", "openapi"])
        self.assertEqual(rc, 1)
        self.assertIn("SPECSTREAM_API_KEY is required", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
