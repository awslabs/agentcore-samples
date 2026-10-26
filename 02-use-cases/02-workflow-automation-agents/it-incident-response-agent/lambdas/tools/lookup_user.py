"""Gateway tool: lookup_user.

Returns user profile, quotas, and recent incident history for a given user_id.
The agent uses this to understand requester context and detect recurring incidents.
"""

import decimal
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

USERS_TABLE = os.environ["USERS_TABLE"]
TICKETS_TABLE = os.environ["TICKETS_TABLE"]

_ddb = boto3.resource("dynamodb")
_users = _ddb.Table(USERS_TABLE)
_tickets = _ddb.Table(TICKETS_TABLE)

# Input validation constants
MAX_USER_ID_LENGTH = 128
USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-\.@]+$")


# Gateway Lambda targets return the tool result DIRECTLY to the model — no
# API-Gateway-style {statusCode, body} envelope. Errors are returned as a
# plain {"error": ...} object so the model can read them.
def _ok(body: dict) -> dict:
    return body


def _err(message: str) -> dict:
    return {"error": message}


def _decimals_to_native(obj):
    """Recursively convert DynamoDB Decimal values to int/float.

    The boto3 DynamoDB resource deserializes Numbers as Decimal, which is not
    JSON-serializable. AWS Lambda serializes the handler's return value to JSON,
    so unconverted Decimals would raise "Object of type Decimal is not JSON
    serializable" and fail the tool call.
    """
    if isinstance(obj, decimal.Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, dict):
        return {k: _decimals_to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimals_to_native(i) for i in obj]
    return obj


def lambda_handler(event, context):
    """Look up user profile and recent ticket history."""
    # STEP: ENRICH — Gather requester context for the agent's reasoning
    logger.info("lookup_user invoked")

    user_id = event.get("user_id")
    if not user_id:
        return _err("user_id is required")

    # Input validation: prevent excessively long or malformed user IDs
    if not isinstance(user_id, str):
        return _err("user_id must be a string")
    if len(user_id) > MAX_USER_ID_LENGTH:
        return _err(f"user_id exceeds maximum length of {MAX_USER_ID_LENGTH} characters")
    if not USER_ID_PATTERN.match(user_id):
        return _err("user_id contains invalid characters (allowed: alphanumeric, _, -, ., @)")

    user = _users.get_item(Key={"user_id": user_id}).get("Item")
    if not user:
        return _err(f"user_id {user_id} not found")

    # Query recent tickets (last 30 days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = _tickets.query(
        IndexName="byRequester",
        KeyConditionExpression=(Key("requester_id").eq(user_id) & Key("created_at").gte(cutoff)),
        Limit=10,
        ScanIndexForward=False,
    ).get("Items", [])

    return _ok(
        _decimals_to_native(
            {
                "user_id": user_id,
                "profile": user,
                "quotas": user.get("quotas", {}),
                "recent_tickets": [
                    {
                        "ticket_id": t["ticket_id"],
                        "title": t.get("title"),
                        "status": t.get("status"),
                        "created_at": t.get("created_at"),
                    }
                    for t in recent
                ],
                "recent_incident_count_30d": len(recent),
            }
        )
    )
