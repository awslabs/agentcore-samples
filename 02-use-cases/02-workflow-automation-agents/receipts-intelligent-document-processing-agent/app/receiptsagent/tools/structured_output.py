"""Structured-output tool for the extractor agent.

Instead of hoping the LLM returns clean JSON, the agent calls `submit_expense`
with typed fields. The tool captures the structured result + runs a deterministic
reconciliation check (totals add up?) so the orchestrator gets a machine-checkable
answer (spec §7). The validator agent (Phase 4) consumes this.

Concurrency note: state is module-level globals reset per invocation via
`reset_state()` — fine for the single-in-flight Runtime model, like the claims
sample. Not safe for concurrent invocations sharing one container.
"""

import json

from strands import tool

_last_expense: dict = {}
_last_validation: dict = {}


def get_last_expense() -> dict:
    return dict(_last_expense)


def get_last_validation() -> dict:
    return dict(_last_validation)


def reset_state() -> None:
    global _last_expense, _last_validation
    _last_expense = {}
    _last_validation = {}


def _reconciles(subtotal, tax, tip, total, tol: float = 0.02) -> bool:
    """True if subtotal + tax + tip ~= total (within a small tolerance)."""
    try:
        parts = sum(float(x or 0) for x in (subtotal, tax, tip))
        return abs(parts - float(total)) <= max(tol, 0.01 * float(total))
    except (TypeError, ValueError):
        return False


@tool
def submit_expense(
    merchant: str,
    transaction_date: str,
    currency: str,
    subtotal: float,
    tax: float,
    tip: float,
    total: float,
    category: str,
    confidence: int,
    line_items: str = "[]",
    payment_method: str = "",
) -> str:
    """Submit the structured expense extracted from the receipt. Call this ONCE
    after reading the OCR output.

    Args:
        merchant: Normalized merchant name.
        transaction_date: ISO 8601 date (YYYY-MM-DD).
        currency: ISO currency code (e.g. USD).
        subtotal: Pre-tax subtotal.
        tax: Tax amount.
        tip: Tip amount (0 if none).
        total: Grand total.
        category: Inferred expense category.
        confidence: Your 0-100 confidence in this extraction.
        line_items: JSON array string of {description, qty, unitPrice, amount}.
        payment_method: Payment method if shown.
    """
    global _last_expense
    try:
        items = json.loads(line_items) if isinstance(line_items, str) else (line_items or [])
    except json.JSONDecodeError:
        items = []

    _last_expense = {
        "merchant": merchant,
        "transaction_date": transaction_date,
        "currency": currency,
        "subtotal": subtotal,
        "tax": tax,
        "tip": tip,
        "total": total,
        "category": category,
        "confidence": max(0, min(100, int(confidence))),
        "line_items": items,
        "payment_method": payment_method,
        "reconciles": _reconciles(subtotal, tax, tip, total),
    }
    return json.dumps(
        {"status": "recorded", "reconciles": _last_expense["reconciles"], "confidence": _last_expense["confidence"]}
    )


@tool
def submit_validation(routing: str, confidence: int, notes: str, concerns: str = "None") -> str:
    """Submit your independent validation of the extracted expense. Call this ONCE
    after reviewing the extractor's output.

    Args:
        routing: Exactly AUTO_PERSIST or NEEDS_REVIEW.
        confidence: Your 0-100 confidence that the extraction is correct and safe to persist.
        notes: Brief assessment of the extraction.
        concerns: Red flags (totals not reconciling, category mismatch, vague merchant), or "None".
    """
    global _last_validation
    r = routing.upper()
    if r not in ("AUTO_PERSIST", "NEEDS_REVIEW"):
        r = "NEEDS_REVIEW"  # fail safe — when unsure, review
    _last_validation = {
        "routing": r,
        "confidence": max(0, min(100, int(confidence))),
        "notes": notes,
        "concerns": concerns,
    }
    return json.dumps({"status": "recorded", "routing": r, "confidence": _last_validation["confidence"]})
