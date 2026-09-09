"""GET /reviews/{task_id} — adjuster detail (Cognito + adjuster group)."""

import json
import os
from decimal import Decimal

import boto3

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
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
        task_id = event["pathParameters"]["task_id"]
        item = table.get_item(Key={"task_id": task_id}).get("Item")
        if item is None:
            return _resp(404, {"error": f"No review task: {task_id}"})
        return _resp(200, _decode(item))
    except Exception as e:  # noqa: BLE001 - handler boundary
        print(f"get_review error: {e}")
        return _resp(500, {"error": "Internal server error"})
