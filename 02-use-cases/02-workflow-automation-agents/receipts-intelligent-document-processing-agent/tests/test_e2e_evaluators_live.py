"""End-to-end: the deployed code-based evaluators, called by AgentCore (no mocks).

The evaluation harness runs the evaluator code in-process, and only the threshold monitor is
exercised live by the online config. This calls all three deployed evaluators through the
AgentCore Evaluate API, so AgentCore invokes each evaluator Lambda with the event it really
sends: the reference answer in `expectedResponse`, the session's spans, and on this path the
evaluator's name.

Each case is one hand-built invocation span carrying the receipts.* attributes the agent
stamps, so the test needs no pipeline run and no model. The expected labels are the ones the
local handler gives for the same input, which unit tests already cover.

Requires a deployed stack; skips cleanly otherwise.
"""

import json
import os
import uuid

import boto3
import pytest

REGION = os.environ.get("AWS_REGION", "us-west-2")
PREFIX = os.environ.get("RECEIPTS_EVALUATOR_PREFIX", "ReceiptsAgent_")

pytestmark = pytest.mark.e2e

LABEL = {
    "merchant": "Blue Bottle Coffee",
    "transaction_date": "2026-06-23",
    "currency": "USD",
    "subtotal": 12.75,
    "tax": 1.15,
    "tip": 2.0,
    "total": 15.9,
    "expected_outcome": "processed",
}


@pytest.fixture(scope="module")
def evaluators():
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    try:
        found = control.list_evaluators()["evaluators"]
    except Exception:
        pytest.skip("AgentCore evaluators not reachable")
    ids = {
        e["evaluatorName"].removeprefix(PREFIX): e["evaluatorId"]
        for e in found
        if e["evaluatorName"].startswith(PREFIX)
    }
    if not {"ReceiptsExtractionAccuracy", "ReceiptsRoutingOutcome", "ReceiptsThresholdControl"} <= set(ids):
        pytest.skip("the receipts evaluators are not deployed")
    return ids


def _session(**overrides) -> tuple[str, list[dict]]:
    """One invocation span with the attributes a correctly extracted, saved receipt carries."""
    attributes = {
        "receipts.status": "processed",
        "receipts.total": 15.9,
        "receipts.merchant": "Blue Bottle Coffee",
        "receipts.transaction_date": "2026-06-23",
        "receipts.currency": "USD",
        "receipts.subtotal": 12.75,
        "receipts.tax": 1.15,
        "receipts.tip": 2.0,
    }
    attributes.update(overrides)
    session_id = f"evaluator-live-{uuid.uuid4().hex}"
    attributes["session.id"] = session_id
    span = {
        "resource": {"attributes": {"service.name": "ReceiptsAgent_receiptsagent.DEFAULT"}},
        "scope": {"name": "opentelemetry.instrumentation.starlette"},
        "traceId": uuid.uuid4().hex,
        "spanId": uuid.uuid4().hex[:16],
        "name": "POST /invocations",
        "kind": "SERVER",
        "startTimeUnixNano": 1_790_000_000_000_000_000,
        "endTimeUnixNano": 1_790_000_020_000_000_000,
        "attributes": attributes,
        "status": {"code": "OK"},
    }
    return session_id, [span]


def _evaluate(evaluator_id: str, session: tuple[str, list[dict]], label: dict | None = None) -> dict:
    session_id, spans = session
    kwargs = {"evaluatorId": evaluator_id, "evaluationInput": {"sessionSpans": spans}}
    if label is not None:
        kwargs["evaluationReferenceInputs"] = [
            {"context": {"spanContext": {"sessionId": session_id}}, "expectedResponse": {"text": json.dumps(label)}}
        ]
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    results = client.evaluate(**kwargs).get("evaluationResults") or []
    assert results, "the evaluator returned no result"
    return results[0]


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, "exact"),
        ({"receipts.transaction_date": "2026-06-30"}, "field_error"),
        ({"receipts.total": 16.9}, "minor_error"),
        ({"receipts.total": 159.0}, "material_error"),
    ],
)
def test_extraction_accuracy(evaluators, overrides, expected):
    result = _evaluate(evaluators["ReceiptsExtractionAccuracy"], _session(**overrides), LABEL)
    assert result.get("label") == expected, result


@pytest.mark.parametrize(
    "overrides, label_overrides, expected",
    [
        ({}, {}, "AutoPersistCorrect"),
        ({"receipts.status": "needs_review"}, {}, "FalseAlarm"),
        ({}, {"expected_outcome": "needs_review"}, "FalseClear"),
        ({"receipts.status": "needs_review"}, {"expected_outcome": "needs_review"}, "ReviewCorrect"),
        # The validator is judged on what it was shown: a wrong extraction makes review right.
        ({"receipts.status": "needs_review", "receipts.transaction_date": "2026-06-30"}, {}, "ReviewCorrect"),
    ],
)
def test_routing_outcome(evaluators, overrides, label_overrides, expected):
    result = _evaluate(evaluators["ReceiptsRoutingOutcome"], _session(**overrides), {**LABEL, **label_overrides})
    assert result.get("label") == expected, result


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, "not_engaged"),
        ({"receipts.total": 2400.0, "receipts.status": "needs_review"}, "held"),
        ({"receipts.total": 2400.0}, "breach"),
    ],
)
def test_threshold_control(evaluators, overrides, expected):
    result = _evaluate(evaluators["ReceiptsThresholdControl"], _session(**overrides))
    assert result.get("label") == expected, result


def test_missing_label_is_reported_not_scored(evaluators):
    result = _evaluate(evaluators["ReceiptsRoutingOutcome"], _session(), {"note": "no expected outcome"})
    assert not result.get("label"), result
    assert "expected_outcome" in (result.get("errorMessage") or ""), result
