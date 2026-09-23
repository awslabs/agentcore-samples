"""Gateway tool: get_recent_expenses — list a user's recent expenses for dedup.

Queries the Expenses table by userId (the partition key), newest first, so the
agent can spot a likely duplicate before persisting (spec §9 step 6).
"""

import json
import os

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("EXPENSES_TABLE", "ReceiptsAgent-Expenses"))


def handler(event, context):
    user_id = event.get("user_id", "")
    if not user_id:
        return json.dumps({"error": "user_id is required"})

    limit = int(event.get("limit", 20))
    limit = max(1, min(limit, 100))

    resp = table.query(
        KeyConditionExpression=Key("userId").eq(user_id),
        ScanIndexForward=False,  # newest expenseId first
        Limit=limit,
    )
    items = resp.get("Items", [])
    return json.dumps({"userId": user_id, "count": len(items), "expenses": items}, default=str)
