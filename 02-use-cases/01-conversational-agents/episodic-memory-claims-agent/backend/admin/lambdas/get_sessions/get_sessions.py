import json
import os

import boto3

TABLE_NAME = os.environ["TABLE_NAME"]

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)

ACTOR_ID_INDEX = "actor_id-index"


def handler(event, context):
    try:
        actor_id = (event.get("queryStringParameters") or {}).get("actorId", "")

        if not actor_id:
            return {
                "statusCode": 400,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
                },
                "body": json.dumps({"error": "actorId parameter is required"}),
            }

        items = []
        kwargs = {
            "IndexName": ACTOR_ID_INDEX,
            "KeyConditionExpression": boto3.dynamodb.conditions.Key("actor_id").eq(actor_id),
        }
        while True:
            resp = table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

        rows = [
            {
                "session_id": i.get("session_id"),
                "title": i.get("session_title"),
                "created_at": i.get("created_at"),
                "actor_id": i.get("actor_id"),
            }
            for i in items
        ]
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            },
            "body": json.dumps({"sessions": rows, "count": len(rows)}),
        }

    except Exception as e:  # noqa: BLE001 - handler boundary
        print(f"Error: {e}")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            },
            "body": json.dumps({"error": "Internal server error"}),
        }
