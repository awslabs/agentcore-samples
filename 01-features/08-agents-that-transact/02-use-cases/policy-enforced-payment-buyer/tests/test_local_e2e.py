from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.request import Request


SAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_ROOT))

from buyer.core import (  # noqa: E402
    AllowlistPolicyAuthorizer,
    PaymentRequirementError,
    PolicyDenied,
    PolicyEnforcedBuyer,
    SimulatedPaymentExecutor,
    _NoRedirect,
)
from buyer.local_demo import (  # noqa: E402
    DEFAULT_AMOUNT,
    DEFAULT_ASSET,
    DEFAULT_NETWORK,
    DEFAULT_PAY_TO,
    InMemorySeller,
)


class PolicyEnforcedBuyerE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = AllowlistPolicyAuthorizer(
            pay_to=DEFAULT_PAY_TO,
            network=DEFAULT_NETWORK,
            asset=DEFAULT_ASSET,
            maximum_amount=DEFAULT_AMOUNT,
        )
        self.payment_executor = SimulatedPaymentExecutor()

    def test_approved_purchase_retries_with_simulated_proof(self) -> None:
        with InMemorySeller() as seller:
            buyer = PolicyEnforcedBuyer(
                policy_authorizer=self.policy,
                payment_executor=self.payment_executor,
                opener=seller.open,
            )
            result = buyer.purchase(f"{seller.base_url}/premium")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.payment_execution, "simulated")
        self.assertEqual(result.settlement, "not-verified")
        self.assertEqual(result.body["content"], "Premium content delivered")
        self.assertEqual(seller.retries, 1)

    def test_amount_above_policy_ceiling_never_retries(self) -> None:
        with InMemorySeller() as seller:
            buyer = PolicyEnforcedBuyer(
                policy_authorizer=self.policy,
                payment_executor=self.payment_executor,
                opener=seller.open,
            )
            with self.assertRaisesRegex(PolicyDenied, "amount"):
                buyer.purchase(f"{seller.base_url}/premium?amount={DEFAULT_AMOUNT + 1}")
            self.assertEqual(seller.retries, 0)

    def test_changed_recipient_never_retries(self) -> None:
        changed_recipient = "0x3333333333333333333333333333333333333333"
        with InMemorySeller() as seller:
            buyer = PolicyEnforcedBuyer(
                policy_authorizer=self.policy,
                payment_executor=self.payment_executor,
                opener=seller.open,
            )
            with self.assertRaisesRegex(PolicyDenied, "recipient"):
                buyer.purchase(f"{seller.base_url}/premium?pay_to={changed_recipient}")
            self.assertEqual(seller.retries, 0)

    def test_default_redirect_handler_does_not_forward_the_request(self) -> None:
        redirect_handler = _NoRedirect()
        redirected = redirect_handler.redirect_request(
            Request("https://seller.example/premium"),
            None,
            302,
            "Found",
            {},
            "https://other.example/premium",
        )

        self.assertIsNone(redirected)

    def test_non_https_resource_is_rejected_before_the_seller_is_contacted(self) -> None:
        with InMemorySeller() as seller:
            buyer = PolicyEnforcedBuyer(
                policy_authorizer=self.policy,
                payment_executor=self.payment_executor,
                opener=seller.open,
            )
            with self.assertRaisesRegex(PaymentRequirementError, "absolute HTTPS"):
                buyer.purchase("http://seller.local/premium")
            self.assertEqual(seller.retries, 0)


if __name__ == "__main__":
    unittest.main()
