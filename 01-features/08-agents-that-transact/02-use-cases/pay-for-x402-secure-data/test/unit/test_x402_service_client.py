from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent" / "container"
sys.path.insert(0, str(AGENT_ROOT))

import x402_secure
import x402_services
from x402_test_fixtures import FakeResponse, FakeSession, service_registry


class X402ServiceClientTests(unittest.TestCase):
    def test_validate_service_operation_rejects_missing_parameters(self):
        with self.assertRaisesRegex(ValueError, "Missing required parameters"):
            x402_services.validate_service_operation(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {},
                service_registry()["heurist_yahoo_finance"]["operations"],
            )

    def test_service_client_reads_default_heurist_url_from_env_at_creation_time(self):
        with patch.dict(
            x402_services.os.environ,
            {"HEURIST_YAHOO_FINANCE_BASE_URL": "https://heurist.example/x402"},
        ):
            registry = x402_services.default_x402_service_registry()

        self.assertEqual(
            registry["heurist_yahoo_finance"]["base_url"],
            "https://heurist.example/x402",
        )

    def test_service_client_returns_402_shape_for_plugin_retry(self):
        session = FakeSession(
            [
                FakeResponse(
                    402,
                    {"x402Version": 1, "accepts": [{"scheme": "exact"}]},
                    text="payment required",
                    headers={"content-type": "application/json"},
                )
            ]
        )
        client = x402_services.X402ServiceClient(
            service_id="heurist_yahoo_finance",
            base_url="https://target.example/x402",
            operations={"quote_snapshot": {"required": {"symbols"}}},
            session=session,
        )

        result = client.call_operation("quote_snapshot", {"symbols": ["AAPL"]})

        self.assertTrue(x402_secure.is_payment_required(result))
        payload = json.loads(result[len(x402_secure.PAYMENT_REQUIRED_MARKER) :])
        self.assertEqual(payload["statusCode"], 402)
        self.assertEqual(payload["body"]["x402Version"], 1)
        self.assertEqual(session.calls[0]["headers"], None)

    def test_service_client_passes_plugin_retry_headers_and_returns_json(self):
        session = FakeSession([FakeResponse(200, {"price": 100})])
        client = x402_services.X402ServiceClient(
            service_id="heurist_yahoo_finance",
            base_url="https://target.example/x402",
            operations={"quote_snapshot": {"required": {"symbols"}}},
            session=session,
        )

        result = client.call_operation(
            "quote_snapshot",
            {"symbols": ["AAPL"]},
            headers={"PAYMENT-SIGNATURE": "signed"},
        )

        self.assertEqual(result, {"price": 100})
        self.assertEqual(session.calls[0]["headers"], {"PAYMENT-SIGNATURE": "signed"})


if __name__ == "__main__":
    unittest.main()
