"""Unit tests for the validator's decision tools (decision.py).

No AWS and no model: the Gateway calls and the note writer are fakes that record what they
were asked to do. What is under test is the guarantees the orchestrator keeps while the
validator acts on its own decision: one outcome, fail safe, review-only when degraded, and
a Cedar denial filed as a review. Also the scorer's trimming of the trace to one agent,
which the note writer's run inside the validator's tool call would otherwise break."""

import os
import sys

import pytest
from decision import CEDAR_BLOCKED_REASON, ReceiptDecision

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))


class _Gateway:
    def __init__(self, denied=False, fail=False):
        self.denied = denied
        self.fail = fail
        self.calls: list[tuple] = []

    def save(self):
        if self.fail:
            raise RuntimeError("gateway down")
        self.calls.append(("save_expense",))
        return {"denied": True} if self.denied else {"saved": True}

    def review(self, reason):
        self.calls.append(("human_review", reason))
        return {"queued": True}


def _decision(gateway):
    return ReceiptDecision(
        save=gateway.save,
        review=gateway.review,
        write_note=lambda reason: f"note: {reason}",
        is_denied=lambda result: bool(result.get("denied")),
    )


def test_approve_saves_the_pinned_expense():
    gateway = _Gateway()
    decision = _decision(gateway)
    assert decision.approve(95, "reconciles") == "Saved."
    assert gateway.calls == [("save_expense",)]
    assert decision.status == "processed"
    assert decision.validation["routing"] == "AUTO_PERSIST"
    assert decision.validation["confidence"] == 95


def test_approve_tool_takes_no_expense_fields():
    tools = {t.tool_name: t for t in _decision(_Gateway()).tools()}
    assert set(tools) == {"approve_expense", "send_to_review"}
    properties = tools["approve_expense"].tool_spec["inputSchema"]["json"]["properties"]
    assert set(properties) == {"confidence", "notes"}


def test_send_to_review_files_the_reviewer_note():
    gateway = _Gateway()
    decision = _decision(gateway)
    decision.send_to_review(40, "off by $2", "total does not reconcile")
    assert gateway.calls == [("human_review", "note: total does not reconcile")]
    assert decision.status == "needs_review"
    assert decision.validation["concerns"] == "total does not reconcile"


def test_a_second_decision_is_refused():
    gateway = _Gateway()
    decision = _decision(gateway)
    decision.send_to_review(40, "off", "total does not reconcile")
    reply = decision.approve(99, "changed my mind")
    assert "already recorded" in reply
    assert [c[0] for c in gateway.calls] == ["human_review"]
    assert decision.status == "needs_review"


def test_no_decision_falls_back_to_review():
    gateway = _Gateway()
    decision = _decision(gateway)
    decision.fallback_review("validator made no decision")
    assert gateway.calls == [("human_review", "note: validator made no decision")]
    assert decision.status == "needs_review"
    assert decision.validation["routing"] == "NEEDS_REVIEW"


def test_fallback_does_nothing_after_a_decision():
    gateway = _Gateway()
    decision = _decision(gateway)
    decision.approve(95, "fine")
    decision.fallback_review("validator made no decision")
    assert gateway.calls == [("save_expense",)]


def test_force_review_offers_only_send_to_review():
    tools = _decision(_Gateway()).tools(allow_approve=False)
    assert [t.tool_name for t in tools] == ["send_to_review"]


def test_cedar_denial_files_a_review():
    gateway = _Gateway(denied=True)
    decision = _decision(gateway)
    reply = decision.approve(95, "fine")
    assert "blocked by policy" in reply
    assert gateway.calls == [("save_expense",), ("human_review", f"note: {CEDAR_BLOCKED_REASON}")]
    assert decision.cedar_blocked is True
    assert decision.status == "needs_review"


def test_gateway_failure_is_recorded_and_raised():
    decision = _decision(_Gateway(fail=True))
    with pytest.raises(RuntimeError):
        decision.approve(95, "fine")
    assert isinstance(decision.error, RuntimeError)


def _span(span_id, name, start, end, parent=None, **attributes):
    return {
        "spanId": span_id,
        "parentSpanId": parent,
        "name": name,
        "startTimeUnixNano": start,
        "endTimeUnixNano": end,
        "attributes": attributes,
    }


def test_scorer_trims_to_the_validator_without_the_note_writer():
    from score_saved import extractor_only, through_validator

    spans = [
        _span("root", "receipts.invocation", 0, 100),
        _span("ext", "invoke_agent extractor", 1, 10, "root", **{"gen_ai.agent.name": "extractor"}),
        _span("val", "invoke_agent validator", 11, 90, "root", **{"gen_ai.agent.name": "validator"}),
        _span("dec", "execute_tool send_to_review", 20, 80, "val", **{"gen_ai.tool.name": "send_to_review"}),
        _span("note", "invoke_agent reviewer-note", 21, 60, "dec", **{"gen_ai.agent.name": "reviewer-note"}),
        _span("chat", "chat", 22, 59, "note"),
        _span(
            "hr",
            "mcp tools/call human-review___human_review",
            61,
            70,
            "dec",
            **{"gen_ai.tool.name": "human-review___human_review"},
        ),
        {"spanId": "note", "timeUnixNano": 30, "body": "the note text"},
    ]
    assert [s["spanId"] for s in through_validator(spans)] == ["root", "ext", "val", "dec"]
    assert [s["spanId"] for s in extractor_only(spans)] == ["root", "ext"]
