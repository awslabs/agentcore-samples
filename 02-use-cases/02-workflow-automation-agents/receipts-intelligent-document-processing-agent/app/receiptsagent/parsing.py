"""Pure payload/parsing helpers — no heavy imports, so they're unit-testable
without the AgentCore runtime or Strands installed.

The claims sample keeps a `parsing.py` for the same reason. Keep this module
dependency-free (stdlib only).
"""

import hashlib
import json
from typing import Any


def receipt_id(s3_uri: str) -> str:
    """Stable per-RECEIPT id = hash(s3_uri). Distinct from the content-derived
    expenseId (user|merchant|date|total): two near-duplicate receipts dedup to one
    EXPENSE but remain two RECEIPTS, so the run ledger keyed on this never collides
    and an error-before-persist still gets its own row."""
    return "rcpt-" + hashlib.sha256((s3_uri or "").encode()).hexdigest()[:16]


def build_run_event(s3_uri: str, user_id: str, result: dict) -> dict:
    """Pure: shape the agent's outcome dict into the run-ledger event detail. Captures
    every fate — processed / needs_review / deferred / error — so the ProcessingRuns
    table records what happened to EVERY receipt. stdlib-only + no AWS, so it's
    unit-testable and shared by the writer Lambda."""
    result = result or {}
    expense = result.get("expense") or {}
    validator = result.get("validator") or {}
    status = result.get("status") or ("error" if result.get("error") else "unknown")
    return {
        "receiptId": receipt_id(s3_uri),
        "s3_uri": s3_uri or "",
        "userId": user_id or "anonymous",
        "status": status,
        "rung": result.get("rung", ""),
        "needs_review": bool(result.get("needs_review", False)),
        "cedar_blocked": bool(result.get("cedar_blocked", False)),
        "step_downs": result.get("step_downs", []),
        "model": result.get("model", ""),
        "extractor_confidence": result.get("extractor_confidence"),
        "parse_rate": result.get("parse_rate"),
        "merchant": expense.get("merchant", ""),
        "total": expense.get("total"),
        "expenseId": expense.get("expense_id", ""),
        "validator_routing": validator.get("routing", ""),
        "validator_concerns": validator.get("concerns", ""),
        "error": result.get("error", ""),
    }


def parse_payload(payload: Any) -> dict:
    """Normalize an invoke payload into a dict.

    Handles the `agentcore dev` wrapper, which delivers `{"prompt": "<json>"}`
    where the inner string is the real JSON payload. Also tolerates a bare JSON
    string and a natural-language prompt.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {"prompt": payload}

    if not isinstance(payload, dict):
        return {"prompt": str(payload)}

    # Unwrap the {"prompt": "<json>"} dev wrapper when it hides the real payload.
    if "prompt" in payload and "s3_uri" not in payload:
        prompt_value = payload.get("prompt")
        if isinstance(prompt_value, str):
            try:
                parsed = json.loads(prompt_value)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass  # natural-language prompt — keep as-is
    return payload
