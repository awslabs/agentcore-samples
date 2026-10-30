from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent" / "container"
sys.path.insert(0, str(AGENT_ROOT))

import agent


class FakeX402ServiceGateway:
    def __init__(self):
        self.calls = []

    def check_x402_endpoint_trust(self, service_id=None, url=None, *, headers=None):
        self.calls.append(("trust", service_id, url, headers))
        return {"overall_score": 92, "risk_level": "low", "is_scam": False}

    def call_trusted_x402_service(self, service_id, operation, payload, *, headers=None):
        self.calls.append(("service", service_id, operation, payload, headers))
        return {"ok": True}


class AgentTests(unittest.TestCase):
    def test_check_x402_endpoint_trust_calls_gateway_with_headers(self):
        fake_gateway = FakeX402ServiceGateway()
        with patch("agent.get_x402_service_gateway", return_value=fake_gateway):
            result = agent.check_x402_endpoint_trust(
                "heurist_yahoo_finance",
                "https://merchant.example/x402",
                headers={"X-PAYMENT": "signed"},
            )

        self.assertEqual(result["overall_score"], 92)
        self.assertEqual(
            fake_gateway.calls,
            [("trust", "heurist_yahoo_finance", "https://merchant.example/x402", {"X-PAYMENT": "signed"})],
        )

    def test_trusted_x402_service_tool_calls_gateway_with_headers(self):
        fake_gateway = FakeX402ServiceGateway()
        with patch("agent.get_x402_service_gateway", return_value=fake_gateway):
            result = agent.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
                headers={"PAYMENT-SIGNATURE": "signed"},
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            fake_gateway.calls,
            [
                (
                    "service",
                    "heurist_yahoo_finance",
                    "quote_snapshot",
                    {"symbols": ["AAPL"]},
                    {"PAYMENT-SIGNATURE": "signed"},
                )
            ],
        )

    def test_create_agent_attaches_payment_plugin_without_raw_http_request(self):
        captured = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_strands = types.SimpleNamespace(Agent=FakeAgent)
        with (
            patch.dict(sys.modules, {"strands": fake_strands}),
            patch("agent.create_agentcore_payments_plugin", return_value="payment-plugin"),
        ):
            result = agent.create_agent()

        self.assertIsInstance(result, FakeAgent)
        self.assertEqual(captured["plugins"], ["payment-plugin"])
        self.assertEqual(
            [tool.__name__ for tool in captured["tools"]],
            ["check_x402_endpoint_trust", "call_trusted_x402_service"],
        )


if __name__ == "__main__":
    unittest.main()
