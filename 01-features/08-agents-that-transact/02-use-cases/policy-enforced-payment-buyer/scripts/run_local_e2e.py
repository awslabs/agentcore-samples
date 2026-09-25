"""Run the local no-side-effect policy-enforced purchase flow."""

from __future__ import annotations

import json
import sys
from pathlib import Path


SAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_ROOT))

from buyer.core import (  # noqa: E402
    AllowlistPolicyAuthorizer,
    PolicyDenied,
    PolicyEnforcedBuyer,
    SimulatedPaymentExecutor,
)
from buyer.local_demo import (  # noqa: E402
    DEFAULT_AMOUNT,
    DEFAULT_ASSET,
    DEFAULT_NETWORK,
    DEFAULT_PAY_TO,
    InMemorySeller,
)


def main() -> None:
    policy = AllowlistPolicyAuthorizer(
        pay_to=DEFAULT_PAY_TO,
        network=DEFAULT_NETWORK,
        asset=DEFAULT_ASSET,
        maximum_amount=DEFAULT_AMOUNT,
    )
    with InMemorySeller() as seller:
        buyer = PolicyEnforcedBuyer(
            policy,
            SimulatedPaymentExecutor(),
            opener=seller.open,
        )
        approved = buyer.purchase(f"{seller.base_url}/premium")
        print(
            json.dumps(
                {
                    "approvedPurchase": {
                        "statusCode": approved.status_code,
                        "paymentExecution": approved.payment_execution,
                        "settlement": approved.settlement,
                        "content": approved.body,
                    }
                },
                indent=2,
            )
        )

        try:
            buyer.purchase(f"{seller.base_url}/premium?amount={DEFAULT_AMOUNT + 1}")
        except PolicyDenied as error:
            print(json.dumps({"overLimitDenied": str(error)}, indent=2))
        else:
            raise RuntimeError("Expected over-limit payment to be denied")

        print(json.dumps({"sellerRetries": seller.retries}, indent=2))


if __name__ == "__main__":
    main()
