import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def handler(event, context):
    try:
        claims = event["requestContext"]["authorizer"]["claims"]
        user_id = claims.get("sub")
        session_id = event["pathParameters"]["session_id"]
        body = json.loads(event["body"])

        existing = table.get_item(Key={"user_id": user_id, "session_id": session_id})
        if "Item" not in existing:
            return {
                "statusCode": 404,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
                },
                "body": json.dumps({"error": "Session not found"}),
            }

        update_expr = "SET updated_at = :updated_at"
        expr_values = {":updated_at": datetime.now(timezone.utc).isoformat()}

        if "session_title" in body:
            update_expr += ", session_title = :title"
            expr_values[":title"] = body["session_title"]

        response = table.update_item(
            Key={"user_id": user_id, "session_id": session_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW",
        )

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
                "Access-Control-Allow-Credentials": True,
            },
            "body": json.dumps(
                {"message": "Session updated", "session": response["Attributes"]},
                cls=DecimalEncoder,
            ),
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
