"""Gateway tool: human_review — record an expense as pending human review.

The HITL tool (spec §7, C1): inline `needs_review` status, no review UI. Writes
the expense row with status=needs_review + the reason, so a reviewer (or a future
review surface) can pick it up. Same Expenses table, same userId partition.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("EXPENSES_TABLE", "ReceiptsAgent-Expenses"))


def _expense_id(user_id: str, merchant: str, date: str, total) -> str:
    raw = f"{user_id}|{merchant}|{date}|{total}".lower()
    return "exp-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


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
    user_id = event.get("user_id", "")
    reason = event.get("reason", "")
    if not user_id or not reason:
        return json.dumps({"error": "user_id and reason are required"})

    merchant = event.get("merchant", "")
    total = event.get("total", "")
    transaction_date = event.get("transaction_date", "")
    expense_id = event.get("expense_id") or _expense_id(user_id, merchant, transaction_date, total)

    item = {
        "userId": user_id,
        "expenseId": expense_id,
        "merchant": merchant,
        "transactionDate": transaction_date,
        "currency": event.get("currency", "USD"),
        "total": str(total),
        "category": event.get("category", ""),
        "lineItems": _to_decimal(event.get("line_items", [])),
        "status": "needs_review",
        "reviewReason": reason,
        "rung": event.get("rung", "L0"),
        "sourceReceiptS3": event.get("source_receipt_s3", ""),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    table.put_item(Item=item)

    return json.dumps(
        {"recorded": True, "userId": user_id, "expenseId": expense_id, "status": "needs_review"},
        default=str,
    )
