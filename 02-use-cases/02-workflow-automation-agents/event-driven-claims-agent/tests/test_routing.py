"""Workflow tests for the dual-agent routing matrix in app/claimsagent/routing.py.

These exercise the confidence-based routing decisions that determine a claim's
fate after the Processor and Validator agents run — the core business logic of
the pipeline — without needing AWS, Strands, or an LLM.

Run:
    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "claimsagent"))

from routing import (  # noqa: E402
    AUTO_APPROVE,
    HUMAN_REVIEW,
    REJECT,
    decide_action,
    resolve_decision,
    resolve_routing,
)


class ResolveDecisionTests(unittest.TestCase):
    def test_structured_accept(self):
        decision = resolve_decision({"decision": "accept"}, "")
        self.assertEqual(decision, "ACCEPT")

    def test_structured_reject(self):
        self.assertEqual(resolve_decision({"decision": "reject"}, ""), "REJECT")

    def test_structured_uppercased(self):
        self.assertEqual(resolve_decision({"decision": "Accept"}, ""), "ACCEPT")

    def test_no_structured_output_defaults_to_reject(self):
        # If the agent fails to call submit_decision, safe default is REJECT.
        self.assertEqual(resolve_decision(None, "some response text"), "REJECT")

    def test_empty_dict_defaults_to_reject(self):
        # An empty dict is falsy → agent didn't call the tool properly.
        self.assertEqual(resolve_decision({}, "DECISION: ACCEPT"), "REJECT")


class ResolveRoutingTests(unittest.TestCase):
    def test_structured_auto_approve(self):
        confidence, routing = resolve_routing({"confidence": 95, "routing": "auto_approve"}, "")
        self.assertEqual(confidence, 95)
        self.assertEqual(routing, AUTO_APPROVE)

    def test_structured_human_review(self):
        confidence, routing = resolve_routing({"confidence": 55, "routing": "human_review"}, "")
        self.assertEqual(confidence, 55)
        self.assertEqual(routing, HUMAN_REVIEW)

    def test_threshold_override_auto_approve_below_threshold(self):
        # Validator says AUTO_APPROVE but confidence is below threshold → override to HUMAN_REVIEW.
        confidence, routing = resolve_routing({"confidence": 60, "routing": "AUTO_APPROVE"}, "")
        self.assertEqual(confidence, 60)
        self.assertEqual(routing, HUMAN_REVIEW)

    def test_threshold_boundary_80_allowed(self):
        confidence, routing = resolve_routing({"confidence": 80, "routing": "AUTO_APPROVE"}, "")
        self.assertEqual(confidence, 80)
        self.assertEqual(routing, AUTO_APPROVE)

    def test_no_structured_output_defaults_to_human_review(self):
        # If the agent fails to call submit_validation, safe default is HUMAN_REVIEW.
        confidence, routing = resolve_routing(None, "some response text")
        self.assertEqual(confidence, 0)
        self.assertEqual(routing, HUMAN_REVIEW)


class DecideActionTests(unittest.TestCase):
    def test_reject_decision_always_wins(self):
        # Even if the validator says auto-approve, a REJECT decision denies the claim.
        self.assertEqual(decide_action(REJECT, AUTO_APPROVE), REJECT)

    def test_reject_decision_beats_human_review(self):
        self.assertEqual(decide_action(REJECT, HUMAN_REVIEW), REJECT)

    def test_accept_with_auto_approve(self):
        self.assertEqual(decide_action("ACCEPT", AUTO_APPROVE), AUTO_APPROVE)

    def test_accept_with_human_review(self):
        self.assertEqual(decide_action("ACCEPT", HUMAN_REVIEW), HUMAN_REVIEW)


class EndToEndRoutingMatrixTests(unittest.TestCase):
    """Full matrix: structured agent outputs → final action."""

    def _run(self, decision, confidence, routing):
        dec = resolve_decision({"decision": decision}, "")
        _, rt = resolve_routing({"confidence": confidence, "routing": routing}, "")
        return decide_action(dec, rt)

    def test_clean_approval(self):
        self.assertEqual(self._run("ACCEPT", 92, "AUTO_APPROVE"), AUTO_APPROVE)

    def test_low_confidence_escalates(self):
        self.assertEqual(self._run("ACCEPT", 55, "HUMAN_REVIEW"), HUMAN_REVIEW)

    def test_rejected_claim_notifies_only(self):
        self.assertEqual(self._run("REJECT", 90, "AUTO_APPROVE"), REJECT)

    def test_high_value_accept_but_flagged_for_review(self):
        # Validator lowered confidence on a high-value claim → human review.
        self.assertEqual(self._run("ACCEPT", 65, "HUMAN_REVIEW"), HUMAN_REVIEW)

    def test_threshold_enforcement_overrides_validator(self):
        # Validator says AUTO_APPROVE at confidence 70, but threshold is 80 → override.
        self.assertEqual(self._run("ACCEPT", 70, "AUTO_APPROVE"), HUMAN_REVIEW)


if __name__ == "__main__":
    unittest.main()
