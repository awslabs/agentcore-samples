from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent" / "container"
sys.path.insert(0, str(AGENT_ROOT))

import x402_secure
import x402_services
from x402_test_fixtures import FakeResponse, FakeServiceClient, FakeTrustClient, service_registry


class X402ServiceGatewayTests(unittest.TestCase):
    def test_gateway_reads_trust_config_from_env_at_creation_time(self):
        with patch.dict(
            x402_services.os.environ,
            {
                "X402_TRUST_THRESHOLD": "77",
                "X402_TRUST_CACHE_TTL_SECONDS": "123",
                "X402_TRUST_FAIL_CLOSED": "0",
            },
        ):
            gateway = x402_services.TrustedX402ServiceGateway(
                services=service_registry(),
                trust_client=FakeTrustClient([{"overall_score": 80}]),
            )

        self.assertEqual(gateway.trust_threshold, 77)
        self.assertEqual(gateway.cache_ttl_seconds, 123)
        self.assertFalse(gateway.fail_closed)

    def test_check_x402_endpoint_trust_stores_result_for_exact_service_url(self):
        trust_client = FakeTrustClient([{"overall_score": 91, "risk_level": "low", "is_scam": False}])
        service_client = FakeServiceClient({"quotes": [{"symbol": "AAPL"}]})
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=trust_client,
            trust_threshold=50,
        )

        with x402_services.use_request_trust_state():
            trust = gateway.check_x402_endpoint_trust(
                service_id="heurist_yahoo_finance",
                headers={"X-PAYMENT": "trust-proof"},
            )
            result = gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
                headers={"PAYMENT-SIGNATURE": "data-proof"},
            )

        self.assertEqual(trust["overall_score"], 91)
        self.assertEqual(result["quotes"][0]["symbol"], "AAPL")
        self.assertEqual(
            trust_client.calls,
            [("https://target.example/x402/yahoo", {"X-PAYMENT": "trust-proof"})],
        )
        self.assertEqual(
            service_client.calls,
            [("quote_snapshot", {"symbols": ["AAPL"]}, {"PAYMENT-SIGNATURE": "data-proof"})],
        )

    def test_request_scoped_trust_does_not_authorize_later_request(self):
        trust_client = FakeTrustClient([{"overall_score": 91, "risk_level": "low", "is_scam": False}])
        service_client = FakeServiceClient({"quotes": [{"symbol": "AAPL"}]})
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=trust_client,
            trust_threshold=50,
        )

        with x402_services.use_request_trust_state():
            gateway.check_x402_endpoint_trust(service_id="heurist_yahoo_finance")
            first_result = gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
            )

        with x402_services.use_request_trust_state():
            second_result = gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["MSFT"]},
            )

        self.assertEqual(first_result["quotes"][0]["symbol"], "AAPL")
        self.assertEqual(second_result["status"], "blocked")
        self.assertIn("trust check is required", second_result["reason"])
        self.assertEqual(
            service_client.calls,
            [("quote_snapshot", {"symbols": ["AAPL"]}, None)],
        )
        self.assertEqual(len(trust_client.calls), 1)

    def test_check_x402_endpoint_trust_accepts_explicit_url_without_authorizing_registry_service(self):
        trust_client = FakeTrustClient([{"overall_score": 91, "risk_level": "low", "is_scam": False}])
        service_client = FakeServiceClient()
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=trust_client,
        )

        with x402_services.use_request_trust_state():
            gateway.check_x402_endpoint_trust(url="https://target.example/x402/other")
            result = gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(service_client.calls, [])

    def test_check_x402_endpoint_trust_returns_402_without_storing_trust(self):
        trust_402 = x402_secure.payment_required_result(
            FakeResponse(402, {"x402Version": 1}, text="payment required", headers={})
        )
        trust_client = FakeTrustClient([trust_402])
        service_client = FakeServiceClient()
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=trust_client,
        )

        with x402_services.use_request_trust_state():
            trust = gateway.check_x402_endpoint_trust(service_id="heurist_yahoo_finance")
            blocked = gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
            )

        self.assertTrue(x402_secure.is_payment_required(trust))
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(service_client.calls, [])

    def test_target_service_blocks_without_prior_trust_check(self):
        service_client = FakeServiceClient()
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=FakeTrustClient([{"overall_score": 91}]),
        )

        with x402_services.use_request_trust_state():
            result = gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("trust check is required", result["reason"])
        self.assertEqual(service_client.calls, [])

    def test_trust_for_service_a_does_not_authorize_service_b(self):
        service_client = FakeServiceClient()
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=FakeTrustClient([{"overall_score": 91, "risk_level": "low", "is_scam": False}]),
        )

        with x402_services.use_request_trust_state():
            gateway.check_x402_endpoint_trust(service_id="paid_research_api")
            result = gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(service_client.calls, [])

    def test_gateway_rejects_unknown_service_before_trust_or_target_payment(self):
        trust_client = FakeTrustClient([{"overall_score": 91}])
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(),
            trust_client=trust_client,
        )

        with self.assertRaisesRegex(ValueError, "Unsupported x402 service"):
            gateway.call_trusted_x402_service("raw_http", "post", {})

        self.assertEqual(trust_client.calls, [])

    def test_gateway_rejects_missing_params_before_trust_or_target_payment(self):
        trust_client = FakeTrustClient([{"overall_score": 91}])
        service_client = FakeServiceClient()
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=trust_client,
        )

        with self.assertRaisesRegex(ValueError, "Missing required parameters"):
            gateway.call_trusted_x402_service("heurist_yahoo_finance", "quote_snapshot", {})

        self.assertEqual(trust_client.calls, [])
        self.assertEqual(service_client.calls, [])

    def test_gateway_blocks_low_score_before_target_payment(self):
        service_client = FakeServiceClient()
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=FakeTrustClient(
                [
                    {
                        "overall_score": 23,
                        "risk_level": "high",
                        "is_scam": False,
                        "scam_indicators": ["new wallet"],
                    }
                ]
            ),
            trust_threshold=50,
        )

        with x402_services.use_request_trust_state():
            gateway.check_x402_endpoint_trust(service_id="heurist_yahoo_finance")
            result = gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("Trust score 23/100", result["reason"])
        self.assertEqual(service_client.calls, [])

    def test_gateway_blocks_scam_before_target_payment(self):
        service_client = FakeServiceClient()
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=FakeTrustClient(
                [
                    {
                        "overall_score": 88,
                        "risk_level": "critical",
                        "is_scam": True,
                        "scam_indicators": ["phishing"],
                    }
                ]
            ),
            trust_threshold=50,
        )

        with x402_services.use_request_trust_state():
            gateway.check_x402_endpoint_trust(service_id="heurist_yahoo_finance")
            result = gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("flagged as scam", result["reason"])
        self.assertEqual(service_client.calls, [])

    def test_gateway_cache_hit_skips_repeated_trust_check(self):
        service_client = FakeServiceClient()
        gateway = x402_services.TrustedX402ServiceGateway(
            services=service_registry(service_client),
            trust_client=FakeTrustClient([{"overall_score": 91, "risk_level": "low", "is_scam": False}]),
            trust_threshold=50,
            cache_ttl_seconds=300,
        )

        with x402_services.use_request_trust_state():
            gateway.check_x402_endpoint_trust(service_id="heurist_yahoo_finance")
            gateway.check_x402_endpoint_trust(service_id="heurist_yahoo_finance")
            gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["AAPL"]},
            )
            gateway.call_trusted_x402_service(
                "heurist_yahoo_finance",
                "quote_snapshot",
                {"symbols": ["MSFT"]},
            )

        self.assertEqual(len(gateway.trust_client.calls), 1)
        self.assertEqual(len(service_client.calls), 2)


if __name__ == "__main__":
    unittest.main()
