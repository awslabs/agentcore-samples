"""Unit tests for the run-ledger event builder + receiptId (the operational-audit
feature). Pure functions, no AWS. The live emit->writer->table path is covered by
tests/test_e2e_runledger_live.py."""

import pytest

from parsing import build_run_event, receipt_id

pytestmark = pytest.mark.unit


def test_receipt_id_stable_and_keyed_on_uri():
    a = receipt_id("s3://b/receipts/sroie/012.jpg")
    assert a == receipt_id("s3://b/receipts/sroie/012.jpg")  # deterministic
    assert a != receipt_id("s3://b/receipts/sroie/016.jpg")  # per-receipt, not content
    assert a.startswith("rcpt-")


def test_processed_run_event():
    result = {
        "status": "processed",
        "rung": "L0",
        "needs_review": False,
        "cedar_blocked": False,
        "model": "global.anthropic.claude-opus-4-8",
        "extractor_confidence": 92,
        "parse_rate": 1.0,
        "step_downs": [],
        "validator": {"routing": "AUTO_PERSIST"},
        "expense": {"merchant": "MR D.I.Y.", "total": 30.91, "expense_id": "exp-abc"},
    }
    ev = build_run_event("s3://b/receipts/u/r.jpg", "u", result)
    assert ev["status"] == "processed"
    assert ev["receiptId"] == receipt_id("s3://b/receipts/u/r.jpg")
    assert ev["merchant"] == "MR D.I.Y." and ev["total"] == 30.91
    assert ev["expenseId"] == "exp-abc" and ev["validator_routing"] == "AUTO_PERSIST"


def test_needs_review_carries_concerns():
    result = {
        "status": "needs_review",
        "rung": "L0",
        "needs_review": True,
        "validator": {"routing": "NEEDS_REVIEW", "concerns": "totals don't reconcile"},
        "expense": {"merchant": "X", "total": 9.0},
    }
    ev = build_run_event("s3://b/r.jpg", "u", result)
    assert ev["status"] == "needs_review"
    assert ev["validator_concerns"] == "totals don't reconcile"


def test_error_before_persist_is_captured():
    # The case that left NO row before: an error with no expense at all.
    ev = build_run_event("s3://b/r.jpg", "u", {"error": "OCR failed: boom", "rung": "L0"})
    assert ev["status"] == "error"
    assert ev["error"] == "OCR failed: boom"
    assert ev["merchant"] == "" and ev["expenseId"] == ""


def test_deferred_run_event():
    ev = build_run_event("s3://b/r.jpg", "u", {"status": "deferred", "rung": "L4", "needs_review": True})
    assert ev["status"] == "deferred" and ev["rung"] == "L4"


def test_none_result_degrades_to_unknown():
    ev = build_run_event("s3://b/r.jpg", "u", None)
    assert ev["status"] == "unknown"
    assert ev["receiptId"].startswith("rcpt-")
