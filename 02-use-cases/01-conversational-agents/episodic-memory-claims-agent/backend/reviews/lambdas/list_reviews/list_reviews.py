"""GET /reviews?status=OPEN — adjuster queue (Cognito + adjuster group)."""

import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
STATUS_INDEX = "status-created_at-index"
_JSON_FIELDS = ("claim", "signals", "transcript_ref", "resolution")


class _Dec(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
        },
        "body": json.dumps(body, cls=_Dec),
    }


def _groups(event):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    raw = claims.get("cognito:groups", "")
    if isinstance(raw, list):
        return raw
    return raw.replace("[", "").replace("]", "").replace(",", " ").split()


def _decode(item):
    out = dict(item)
    for f in _JSON_FIELDS:
        if isinstance(out.get(f), str):
            try:
                out[f] = json.loads(out[f])
            except (ValueError, TypeError):
                pass
    return out


def handler(event, context):
    if "adjuster" not in _groups(event):
        return _resp(403, {"error": "Adjuster group required"})
    try:
        status = (event.get("queryStringParameters") or {}).get("status", "OPEN").upper()
        resp = table.query(
            IndexName=STATUS_INDEX,
            KeyConditionExpression=Key("status").eq(status),
            ScanIndexForward=True,
        )
        tasks = [_decode(i) for i in resp.get("Items", [])]
        return _resp(200, {"tasks": tasks, "count": len(tasks), "status": status})
    except Exception as e:  # noqa: BLE001 - handler boundary
        print(f"list_reviews error: {e}")
        return _resp(500, {"error": "Internal server error"})
