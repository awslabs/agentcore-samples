"""Unit tests for the code-based evaluators (no AWS).

`handler` routes on the evaluator name or id, for local and on-demand use: the online path
passes only the id, the on-demand path the name, and both must reach the same metric. Each
deployed evaluator Lambda has its own entry point, which needs neither."""

import json
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


# Routing grades the validator's own decision, read from its decision tool call, not the
# receipt's final status. The two differ when Cedar blocks a save the validator approved.
OVER_LIMIT_LABEL = {"total": 2400.0, "expected_outcome": "needs_review"}
CEDAR_HELD = {"attributes": {"receipts.status": "needs_review", "receipts.total": 2400.0}}
DENIED_SAVE = {"name": "mcp tools/call save-expense___save_expense", "status": {"code": "ERROR"}}


def _decision(tool: str) -> dict:
    return {"name": f"execute_tool {tool}", "attributes": {"gen_ai.tool.name": tool}, "startTimeUnixNano": 1}


def _routing(spans, label):
    from business_outcomes import routing_outcome_handler

    reference = {"context": {"spanContext": {"sessionId": "s"}}, "expectedResponse": {"text": json.dumps(label)}}
    return routing_outcome_handler.unwrapped(
        EvaluatorInput(evaluation_level="SESSION", session_spans=spans, reference_inputs=[reference]), None
    )


def test_an_approval_cedar_blocked_is_the_validators_false_clear():
    out = _routing([CEDAR_HELD, _decision("approve_expense"), DENIED_SAVE], OVER_LIMIT_LABEL)
    assert out.label == "FalseClear"
    assert "Cedar policy blocked the save" in out.explanation


def test_the_validators_own_review_is_review_correct():
    out = _routing([CEDAR_HELD, _decision("send_to_review")], OVER_LIMIT_LABEL)
    assert out.label == "ReviewCorrect"


def test_a_trace_without_a_decision_call_falls_back_to_the_status():
    out = _routing([CEDAR_HELD], OVER_LIMIT_LABEL)
    assert out.label == "ReviewCorrect"


# Extraction accuracy: a field the label has but the extraction left empty is wrong.
DATE_LABEL = {"total": 196.20, "transaction_date": "2026-06-29", "merchant": "Harbor Medical Supply", "tip": 0.0}


def _extraction(attributes, label):
    from business_outcomes import extraction_accuracy_handler

    reference = {"context": {"spanContext": {"sessionId": "s"}}, "expectedResponse": {"text": json.dumps(label)}}
    return extraction_accuracy_handler.unwrapped(
        EvaluatorInput(
            evaluation_level="SESSION", session_spans=[{"attributes": attributes}], reference_inputs=[reference]
        ),
        None,
    )


def test_a_missing_date_is_a_field_error():
    out = _extraction({"receipts.total": 196.20, "receipts.merchant": "Harbor Medical Supply"}, DATE_LABEL)
    assert out.label == "field_error" and "date missing" in out.explanation


def test_a_missing_zero_tip_is_not_an_error():
    attributes = {"receipts.total": 196.20, "receipts.merchant": "Harbor Medical Supply"}
    out = _extraction({**attributes, "receipts.transaction_date": "2026-06-29"}, DATE_LABEL)
    assert out.label == "exact"


# The threshold monitor: a run that ended without being saved or held is not "held".
def test_an_errored_run_over_the_limit_is_no_outcome():
    out = _threshold([{"attributes": {"receipts.status": "error", "receipts.total": 2400.0}}])
    assert out.label == "no_outcome"
