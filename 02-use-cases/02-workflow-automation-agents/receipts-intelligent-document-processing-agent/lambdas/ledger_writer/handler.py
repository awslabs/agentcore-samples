"""Ledger-writer Lambda: EventBridge ReceiptProcessed event -> ProcessingRuns row.

The operational-audit consumer (decoupled from the agent's hot path). The agent fires
one best-effort event per run; this Lambda upserts a row keyed by `receiptId`
(= hash(s3_uri)), so EVERY receipt has exactly one fate row — including an
error-before-persist, which leaves no row in the content-keyed Expenses table.

This is the scalability fix for "what happened to receipt X?": one GetItem here
instead of cross-referencing three CloudWatch log groups by hand. A separate SNS rule
on detail.status=error pushes failures to an admin (see infra-construct.ts).
"""

import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("RUNS_TABLE", "ReceiptsAgent-ProcessingRuns"))


def _to_decimal(obj):
    """DynamoDB rejects Python floats — coerce floats -> Decimal recursively."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [_to_decimal(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    return obj


def handler(event, context):
    """EventBridge delivers one ReceiptProcessed event; upsert its ProcessingRuns row."""
    detail = event.get("detail", {}) if isinstance(event, dict) else {}
    receipt_id = detail.get("receiptId")
    if not receipt_id:
        # Nothing to key on — surface it rather than silently drop.
        return {"skipped": "no receiptId", "detail": str(detail)[:300]}

    item = {
        "receiptId": receipt_id,
        "status": detail.get("status", "unknown"),
        "s3Uri": detail.get("s3_uri", ""),
        "userId": detail.get("userId", "anonymous"),
        "rung": detail.get("rung", ""),
        "needsReview": bool(detail.get("needs_review", False)),
        "cedarBlocked": bool(detail.get("cedar_blocked", False)),
        "stepDowns": detail.get("step_downs", []),
        "model": detail.get("model", ""),
        "extractorConfidence": detail.get("extractor_confidence"),
        "parseRate": detail.get("parse_rate"),
        "merchant": detail.get("merchant", ""),
        "total": str(detail.get("total")) if detail.get("total") is not None else "",
        "expenseId": detail.get("expenseId", ""),
        "validatorRouting": detail.get("validator_routing", ""),
        "validatorConcerns": detail.get("validator_concerns", ""),
        "error": detail.get("error", ""),
        "processedAt": datetime.now(timezone.utc).isoformat(),
    }
    # Drop empty/None values so the row stays clean; coerce floats for DynamoDB.
    item = {k: _to_decimal(v) for k, v in item.items() if v != "" and v is not None}
    table.put_item(Item=item)

    return {"recorded": True, "receiptId": receipt_id, "status": item.get("status")}
