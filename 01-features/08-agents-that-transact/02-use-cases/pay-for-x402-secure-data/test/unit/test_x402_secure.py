from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent" / "container"
sys.path.insert(0, str(AGENT_ROOT))

import x402_secure


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self.responses.pop(0)


class X402SecureTests(unittest.TestCase):
    def test_client_reads_x402_secure_config_from_env_at_creation_time(self):
        with patch.dict(
            x402_secure.os.environ,
            {
                "X402_SECURE_BASE_URL": "https://env-x402-secure.example",
                "X402_SECURE_SCORE_ENDPOINT": "custom/score",
            },
        ):
            client = x402_secure.X402SecureClient()

        self.assertEqual(client.base_url, "https://env-x402-secure.example")
        self.assertEqual(client.score_endpoint_path, "/custom/score")

    def test_score_endpoint_returns_402_marker_for_plugin_retry(self):
        session = FakeSession(
            [
                FakeResponse(
                    402,
                    {
                        "x402Version": 1,
                        "accepts": [{"scheme": "exact", "network": "base-sepolia"}],
                    },
                    text="payment required",
                    headers={"content-type": "application/json"},
                ),
            ]
        )
        client = x402_secure.X402SecureClient(
            base_url="https://x402-secure.example",
            session=session,
        )

        result = client.score_endpoint("https://merchant.example/x402")

        self.assertTrue(x402_secure.is_payment_required(result))
        payload = json.loads(result[len(x402_secure.PAYMENT_REQUIRED_MARKER) :])
        self.assertEqual(payload["statusCode"], 402)
        self.assertEqual(payload["body"]["x402Version"], 1)
        self.assertIsInstance(payload["headers"], dict)
        self.assertEqual(session.calls[0]["url"], "https://x402-secure.example/x402/tools/get_overall_score")
        self.assertEqual(session.calls[0]["json"], {"url": "https://merchant.example/x402"})

    def test_402_marker_is_recognized_by_agentcore_plugin_handler(self):
        # Regression guard: the 402 shape MUST be what AgentCorePaymentsPlugin detects,
        # otherwise it never triggers payment. This runs our output through the real handler.
        try:
            from bedrock_agentcore.payments.integrations.handlers import (
                GenericPaymentHandler,
            )
        except ImportError:  # pragma: no cover - SDK layout may differ across versions
            self.skipTest("bedrock_agentcore GenericPaymentHandler unavailable")

        session = FakeSession(
            [
                FakeResponse(
                    402,
                    {"x402Version": 1, "accepts": [{"scheme": "exact"}]},
                    text="payment required",
                    headers={"content-type": "application/json"},
                ),
            ]
        )
        client = x402_secure.X402SecureClient(
            base_url="https://x402-secure.example",
            session=session,
        )

        result = client.score_endpoint("https://merchant.example/x402")

        # Strands wraps a str tool return as {"content": [{"text": <str>}]}, which is
        # exactly what the plugin inspects to decide whether to process a payment.
        event_result = {"content": [{"text": result}]}
        handler = GenericPaymentHandler()
        self.assertEqual(handler.extract_status_code(event_result), 402)
        self.assertIsInstance(handler.extract_headers(event_result), dict)
        self.assertIsInstance(handler.extract_body(event_result), dict)

    def test_score_endpoint_passes_plugin_retry_headers_and_returns_score(self):
        session = FakeSession(
            [
                FakeResponse(200, {"overall_score": 91, "risk_level": "low", "is_scam": False}),
            ]
        )
        client = x402_secure.X402SecureClient(
            base_url="https://x402-secure.example",
            session=session,
        )

        result = client.score_endpoint(
            "https://merchant.example/x402",
            headers={"PAYMENT-SIGNATURE": "signed"},
        )

        self.assertEqual(result["overall_score"], 91)
        self.assertEqual(session.calls[0]["headers"], {"PAYMENT-SIGNATURE": "signed"})

    def test_score_endpoint_raises_for_non_success_response(self):
        session = FakeSession([FakeResponse(500, {"message": "bad"}, text="bad")])
        client = x402_secure.X402SecureClient(
            base_url="https://x402-secure.example",
            session=session,
        )

        with self.assertRaisesRegex(RuntimeError, "x402-secure score check failed"):
            client.score_endpoint("https://merchant.example/x402")


if __name__ == "__main__":
    unittest.main()
