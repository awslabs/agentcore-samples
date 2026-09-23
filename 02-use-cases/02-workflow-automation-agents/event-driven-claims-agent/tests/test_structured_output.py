"""Unit tests for the structured-output tools in app/claimsagent/tools/structured_output.py.

Covers state capture, confidence clamping, value normalization (uppercasing),
copy semantics, and reset behavior. These tools require the Strands SDK
(`@tool` decorator), so the whole module is skipped when Strands isn't installed
(e.g., running `python3 -m unittest` outside the agent venv). Run under the
agent venv (`app/claimsagent/.venv`) to exercise them.

Run:
    app/claimsagent/.venv/bin/python -m unittest discover -s tests
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "claimsagent"))

try:
    from tools.structured_output import (
        get_last_decision,
        get_last_validation,
        reset_state,
        submit_decision,
        submit_validation,
    )

    _STRANDS_AVAILABLE = True
except ImportError:
    _STRANDS_AVAILABLE = False


@unittest.skipUnless(_STRANDS_AVAILABLE, "Strands SDK not installed (run under agent venv)")
class SubmitDecisionTests(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_records_decision_fields(self):
        submit_decision(
            decision="accept",
            amount=2000,
            policy_number="POL-12345",
            category="auto_collision",
            description="fender bender",
            reasoning="within limits",
            coverage_check="active, $500 deductible",
        )
        captured = get_last_decision()
        self.assertEqual(captured["decision"], "ACCEPT")
        self.assertEqual(captured["amount"], 2000)
        self.assertEqual(captured["policy_number"], "POL-12345")
        self.assertEqual(captured["category"], "auto_collision")

    def test_decision_uppercased(self):
        submit_decision(
            decision="reject",
            amount=0,
            policy_number="POL-1",
            category="theft",
            description="d",
            reasoning="r",
            coverage_check="c",
        )
        self.assertEqual(get_last_decision()["decision"], "REJECT")

    def test_returns_status_json(self):
        result = submit_decision(
            decision="accept",
            amount=100,
            policy_number="POL-1",
            category="medical",
            description="d",
            reasoning="r",
            coverage_check="c",
        )
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "recorded")
        self.assertEqual(parsed["decision"], "ACCEPT")

    def test_get_returns_copy_not_reference(self):
        submit_decision(
            decision="accept",
            amount=100,
            policy_number="POL-1",
            category="medical",
            description="d",
            reasoning="r",
            coverage_check="c",
        )
        snapshot = get_last_decision()
        snapshot["decision"] = "MUTATED"
        # Mutating the returned dict must not affect internal state.
        self.assertEqual(get_last_decision()["decision"], "ACCEPT")


@unittest.skipUnless(_STRANDS_AVAILABLE, "Strands SDK not installed (run under agent venv)")
class SubmitValidationTests(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_records_validation_fields(self):
        submit_validation(
            confidence=92,
            routing="auto_approve",
            validation_notes="clean",
            concerns="None",
        )
        captured = get_last_validation()
        self.assertEqual(captured["confidence"], 92)
        self.assertEqual(captured["routing"], "AUTO_APPROVE")

    def test_confidence_clamped_above_100(self):
        submit_validation(confidence=150, routing="auto_approve", validation_notes="n", concerns="None")
        self.assertEqual(get_last_validation()["confidence"], 100)

    def test_confidence_clamped_below_0(self):
        submit_validation(confidence=-10, routing="human_review", validation_notes="n", concerns="c")
        self.assertEqual(get_last_validation()["confidence"], 0)

    def test_routing_uppercased(self):
        submit_validation(confidence=50, routing="human_review", validation_notes="n", concerns="c")
        self.assertEqual(get_last_validation()["routing"], "HUMAN_REVIEW")


@unittest.skipUnless(_STRANDS_AVAILABLE, "Strands SDK not installed (run under agent venv)")
class ResetStateTests(unittest.TestCase):
    def test_reset_clears_both(self):
        submit_decision(
            decision="accept",
            amount=1,
            policy_number="POL-1",
            category="auto",
            description="d",
            reasoning="r",
            coverage_check="c",
        )
        submit_validation(confidence=90, routing="auto_approve", validation_notes="n", concerns="None")
        reset_state()
        self.assertEqual(get_last_decision(), {})
        self.assertEqual(get_last_validation(), {})

    def test_empty_state_before_submit(self):
        reset_state()
        self.assertEqual(get_last_decision(), {})
        self.assertEqual(get_last_validation(), {})


if __name__ == "__main__":
    unittest.main()
