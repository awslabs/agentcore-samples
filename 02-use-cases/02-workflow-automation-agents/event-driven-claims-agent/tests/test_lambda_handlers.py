"""Unit tests for Gateway Lambda tool handlers (input validation + happy path).

Covers the validation logic in lambdas/create_claim and lambdas/policy_lookup.
DynamoDB calls are mocked, so no AWS access is needed. Validation-failure paths
return before any AWS call; success paths patch the module-level `table`.

The two handlers are both named `handler.py`, so they're loaded under unique
module names via importlib to avoid collision.

Run:
    python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import unittest
from unittest.mock import MagicMock

# boto3.resource() can require a region at creation time; set one before import.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")

_ROOT = os.path.join(os.path.dirname(__file__), "..")

try:
    import boto3  # noqa: F401

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False


def _load(module_name: str, rel_path: str):
    """Load a handler module from an explicit path under a unique name."""
    path = os.path.join(_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class CreateClaimValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("create_claim_handler", "lambdas/create_claim/handler.py")

    def setUp(self):
        # Fresh mock table per test; restore after.
        self._orig_table = self.mod.table
        self.mod.table = MagicMock()

    def tearDown(self):
        self.mod.table = self._orig_table

    def _call(self, event):
        return json.loads(self.mod.handler(event, None))

    def test_missing_policy_number(self):
        result = self._call({"description": "damage"})
        self.assertIn("error", result)
        self.mod.table.put_item.assert_not_called()

    def test_missing_description(self):
        result = self._call({"policy_number": "POL-1"})
        self.assertIn("error", result)
        self.mod.table.put_item.assert_not_called()

    def test_description_too_long(self):
        result = self._call(
            {
                "policy_number": "POL-1",
                "description": "x" * 5001,
            }
        )
        self.assertIn("error", result)
        self.assertIn("5000", result["error"])
        self.mod.table.put_item.assert_not_called()

    def test_negative_amount(self):
        result = self._call(
            {
                "policy_number": "POL-1",
                "description": "d",
                "estimated_amount": -5,
            }
        )
        self.assertIn("error", result)
        self.mod.table.put_item.assert_not_called()

    def test_amount_exceeds_max(self):
        result = self._call(
            {
                "policy_number": "POL-1",
                "description": "d",
                "estimated_amount": 10_000_001,
            }
        )
        self.assertIn("error", result)
        self.mod.table.put_item.assert_not_called()

    def test_valid_claim_creates_record(self):
        result = self._call(
            {
                "policy_number": "POL-12345",
                "description": "fender bender",
                "estimated_amount": 2000,
                "category": "auto_collision",
            }
        )
        self.assertNotIn("error", result)
        self.assertTrue(result["claim_id"].startswith("CLM-"))
        self.assertEqual(result["policy_number"], "POL-12345")
        self.assertEqual(result["category"], "auto_collision")
        self.mod.table.put_item.assert_called_once()

    def test_agent_routed_status_passthrough(self):
        # The agent controls status/decision; the Lambda just records them.
        result = self._call(
            {
                "policy_number": "POL-1",
                "description": "d",
                "estimated_amount": 100,
                "status": "approved",
                "decision": "auto_approved",
            }
        )
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["decision"], "auto_approved")

    def test_default_status_pending_review(self):
        result = self._call(
            {
                "policy_number": "POL-1",
                "description": "d",
                "estimated_amount": 100,
            }
        )
        self.assertEqual(result["status"], "pending_review")


@unittest.skipUnless(_BOTO3_AVAILABLE, "boto3 not installed")
class PolicyLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load("policy_lookup_handler", "lambdas/policy_lookup/handler.py")

    def setUp(self):
        self._orig_table = self.mod.table
        self.mod.table = MagicMock()

    def tearDown(self):
        self.mod.table = self._orig_table

    def _call(self, event):
        return json.loads(self.mod.handler(event, None))

    def test_missing_policy_number(self):
        result = self._call({})
        self.assertIn("error", result)
        self.mod.table.get_item.assert_not_called()

    def test_policy_not_found(self):
        self.mod.table.get_item.return_value = {}  # no Item
        result = self._call({"policy_number": "POL-NOPE"})
        self.assertIn("error", result)
        self.assertIn("not found", result["error"].lower())

    def test_policy_found_returns_item(self):
        self.mod.table.get_item.return_value = {
            "Item": {
                "policy_number": "POL-12345",
                "holder": "John Smith",
                "status": "active",
                "coverage_limit": 50000,
            }
        }
        result = self._call({"policy_number": "POL-12345"})
        self.assertEqual(result["policy_number"], "POL-12345")
        self.assertEqual(result["status"], "active")


if __name__ == "__main__":
    unittest.main()
