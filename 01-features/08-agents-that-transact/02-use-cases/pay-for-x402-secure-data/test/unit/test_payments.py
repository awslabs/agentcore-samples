from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent" / "container"
sys.path.insert(0, str(AGENT_ROOT))

import payments


class PaymentTests(unittest.TestCase):
    def test_missing_required_invocation_context_raises(self):
        with (
            payments.use_invocation_payment_context(None, require_payment_context=True),
            self.assertRaisesRegex(ValueError, "payment_context"),
        ):
            payments.resolve_payment_plugin_config_values()

    def test_resolve_payment_plugin_config_from_request_context(self):
        context = payments.PaymentContext("user", "session", "instrument")
        with (
            patch.dict(
                payments.os.environ,
                {
                    "MANAGER_ARN": "manager",
                    "AWS_REGION": "us-east-2",
                    "AGENT_NAME": "agent-name",
                    "PAYMENT_CONNECTOR_ID": "connector",
                },
                clear=False,
            ),
            payments.use_invocation_payment_context(context, require_payment_context=True),
        ):
            config = payments.resolve_payment_plugin_config_values()

        self.assertIsNotNone(config)
        self.assertEqual(config.payment_manager_arn, "manager")
        self.assertEqual(config.user_id, "user")
        self.assertEqual(config.payment_session_id, "session")
        self.assertEqual(config.payment_instrument_id, "instrument")
        self.assertEqual(config.region, "us-east-2")
        self.assertEqual(config.agent_name, "agent-name")
        self.assertEqual(config.payment_connector_id, "connector")

    def test_resolve_payment_plugin_config_returns_none_without_context_or_env(self):
        with patch.dict(payments.os.environ, {}, clear=True):
            self.assertIsNone(payments.resolve_payment_plugin_config_values())


if __name__ == "__main__":
    unittest.main()
