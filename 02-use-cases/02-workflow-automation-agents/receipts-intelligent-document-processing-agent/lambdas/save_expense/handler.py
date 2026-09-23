"""Gateway tool: save_expense — persist a validated expense for a user.

The Cedar-gated write (spec §5.5: BlockExcessiveExpense gates this at the Gateway
before the Lambda runs). Per-user separation is the partition key: every row is
written under the userId the tool was handed.

Idempotency / dedup (spec §8): expenseId is derived from receipt content
(user + merchant + date + total), so a retried or replayed receipt overwrites the
same row instead of creating a duplicate.
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
    """DynamoDB rejects Python floats. Coerce floats -> Decimal recursively
    (through the nested line_items list) before put_item."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [_to_decimal(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    return obj


def handler(event, context):
    user_id = event.get("user_id", "")
    merchant = event.get("merchant", "")
    total = event.get("total", None)

    if not user_id or not merchant or total is None:
        return json.dumps({"error": "user_id, merchant, and total are required"})

    transaction_date = event.get("transaction_date", "")
    expense_id = event.get("expense_id") or _expense_id(user_id, merchant, transaction_date, total)

    item = {
        "userId": user_id,
        "expenseId": expense_id,
        "merchant": merchant,
        "merchantAddress": event.get("merchant_address", ""),
        "transactionDate": transaction_date,
        "currency": event.get("currency", "USD"),
        "subtotal": str(event.get("subtotal", "")),
        "tax": str(event.get("tax", "")),
        "tip": str(event.get("tip", "")),
        "total": str(total),
        "paymentMethod": event.get("payment_method", ""),
        "lineItems": _to_decimal(event.get("line_items", [])),
        "category": event.get("category", ""),
        "status": event.get("status", "processed"),
        "reviewReason": event.get("review_reason", ""),
        "rung": event.get("rung", "L0"),
        "sourceReceiptS3": event.get("source_receipt_s3", ""),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    table.put_item(Item=item)

    return json.dumps({"saved": True, "userId": user_id, "expenseId": expense_id}, default=str)
