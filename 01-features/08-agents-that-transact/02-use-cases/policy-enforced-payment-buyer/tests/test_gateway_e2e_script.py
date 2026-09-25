from __future__ import annotations

import importlib.util
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch


SAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_ROOT))

from buyer.core import PaymentRequirement, PolicyDenied  # noqa: E402


SCRIPT_PATH = SAMPLE_ROOT / "scripts" / "run_gateway_e2e.py"
SPEC = importlib.util.spec_from_file_location("run_gateway_e2e", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
run_gateway_e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_gateway_e2e)


class RecordingAuthorizer:
    def __init__(self, context: object) -> None:
        del context
        self.requirements: list[PaymentRequirement] = []

    def authorize(self, requirement: PaymentRequirement) -> None:
        self.requirements.append(requirement)
        if len(self.requirements) == 2:
            raise PolicyDenied("POLICY_DENY: recipient")


class GatewayE2EScriptTest(unittest.TestCase):
    def test_calls_gateway_for_original_then_changed_recipient(self) -> None:
        requirement = PaymentRequirement(
            resource_url="https://seller.example/premium",
            pay_to="0x1111111111111111111111111111111111111111",
            amount=1000,
            network="eip155:84532",
            asset="0x2222222222222222222222222222222222222222",
            payment_required_request={},
        )
        authorizer = RecordingAuthorizer(None)

        with (
            patch.dict(
                "os.environ",
                {
                    "AWS_REGION": "us-west-2",
                    "POLICY_GATEWAY_URL": "https://gateway.example/mcp",
                    "POLICY_TARGET_NAME": "PaymentPolicyTools",
                    "X402_RESOURCE_URL": requirement.resource_url,
                },
                clear=True,
            ),
            patch.object(run_gateway_e2e, "fetch_payment_requirement", return_value=requirement),
            patch.object(run_gateway_e2e, "GatewayPolicyAuthorizer", return_value=authorizer),
        ):
            run_gateway_e2e.main()

        self.assertEqual(len(authorizer.requirements), 2)
        self.assertEqual(authorizer.requirements[0], requirement)
        self.assertEqual(
            authorizer.requirements[1].pay_to,
            "0x1111111111111111111111111111111111111110",
        )
        self.assertEqual(authorizer.requirements[1].amount, requirement.amount)
        self.assertEqual(authorizer.requirements[1].network, requirement.network)
        self.assertEqual(authorizer.requirements[1].asset, requirement.asset)

    def test_unreachable_seller_exits_cleanly(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "AWS_REGION": "us-west-2",
                    "POLICY_GATEWAY_URL": "https://gateway.example/mcp",
                    "POLICY_TARGET_NAME": "PaymentPolicyTools",
                    "X402_RESOURCE_URL": "https://seller.example/premium",
                },
                clear=True,
            ),
            patch.object(
                run_gateway_e2e,
                "fetch_payment_requirement",
                side_effect=URLError("seller unavailable"),
            ),
        ):
            stderr = StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                run_gateway_e2e.run()

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("ERROR:", stderr.getvalue())
        self.assertIn("seller unavailable", stderr.getvalue())
