"""Unit tests for the code-based evaluators (no AWS).

`handler` routes on the evaluator name or id, for local and on-demand use: the online path
passes only the id, the on-demand path the name, and both must reach the same metric. Each
deployed evaluator Lambda has its own entry point, which needs neither."""

import os
import sys

import pytest
from bedrock_agentcore.evaluation.custom_code_based_evaluators import EvaluatorInput

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evaluators", "business_outcomes"))
from business_outcomes import handler  # noqa: E402

pytestmark = pytest.mark.unit

SAVED_OVER_LIMIT = [{"attributes": {"receipts.status": "processed", "receipts.total": 2400.0}}]


def _run(**ids):
    return handler.unwrapped(
        EvaluatorInput(evaluation_level="SESSION", session_spans=SAVED_OVER_LIMIT, reference_inputs=[], **ids), None
    )


def test_online_path_routes_on_the_evaluator_id():
    out = _run(evaluator_id="ReceiptsAgent_ReceiptsThresholdControl-cxrwrs9ZLp")
    assert out.label == "breach"


def test_on_demand_path_routes_on_the_evaluator_name():
    out = _run(evaluator_name="ReceiptsAgent_ReceiptsThresholdControl")
    assert out.label == "breach"


def test_unknown_evaluator_is_reported_not_guessed():
    out = _run(evaluator_id="SomethingElse-abcdefghij")
    assert out.errorCode == "UNKNOWN_EVALUATOR"


def test_deployed_entry_points_need_no_name_or_id():
    from business_outcomes import threshold_control_handler

    out = threshold_control_handler.unwrapped(
        EvaluatorInput(evaluation_level="SESSION", session_spans=SAVED_OVER_LIMIT, reference_inputs=[]), None
    )
    assert out.label == "breach"


HELD_OVER_LIMIT = {"attributes": {"receipts.status": "needs_review", "receipts.total": 2400.0}}


def _threshold(spans):
    from business_outcomes import threshold_control_handler

    return threshold_control_handler.unwrapped(
        EvaluatorInput(evaluation_level="SESSION", session_spans=spans, reference_inputs=[]), None
    )


def test_a_denied_save_span_reads_as_blocked_by_the_policy():
    denied = {"name": "mcp tools/call save-expense___save_expense", "status": {"code": "ERROR"}}
    out = _threshold([HELD_OVER_LIMIT, denied])
    assert out.label == "held" and "blocked by the policy" in out.explanation


def test_no_denied_save_reads_as_held_by_the_validator():
    out = _threshold([HELD_OVER_LIMIT])
    assert out.label == "held" and "held by the validator first" in out.explanation
