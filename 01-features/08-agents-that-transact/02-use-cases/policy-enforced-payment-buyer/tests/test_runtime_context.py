from __future__ import annotations

import sys
import unittest
from pathlib import Path


SAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_ROOT))

from buyer.core import PolicyDenied  # noqa: E402
from buyer.runtime_context import runtime_context, validate_seller_url  # noqa: E402


class RuntimeContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {
            "payment_manager_arn": "arn:example",
            "user_id": "test-user",
            "payment_session_id": "session",
            "payment_instrument_id": "instrument",
            "policy_gateway_url": "https://gateway.example",
        }

    def test_requires_a_seller_origin(self) -> None:
        with self.assertRaisesRegex(ValueError, "seller_base_url"):
            runtime_context(self.payload)

    def test_normalizes_and_enforces_the_seller_origin(self) -> None:
        context = runtime_context(
            {**self.payload, "seller_base_url": "https://seller.example/"}
        )

        self.assertEqual(context.seller_base_url, "https://seller.example")
        validate_seller_url("https://seller.example/premium", context.seller_base_url)
        with self.assertRaisesRegex(PolicyDenied, "outside the approved seller origin"):
            validate_seller_url("https://other.example/premium", context.seller_base_url)

    def test_rejects_an_insecure_or_credentialed_seller_origin(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute HTTPS"):
            validate_seller_url("http://seller.example/premium", "http://seller.example")
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            validate_seller_url(
                "https://seller.example/premium",
                "https://user:password@seller.example",
            )
