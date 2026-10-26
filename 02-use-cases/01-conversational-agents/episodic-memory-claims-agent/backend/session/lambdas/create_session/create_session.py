import json
import os
import uuid
from datetime import datetime, timezone

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])


def handler(event, context):
    try:
        # Extract user info from Cognito authorizer
        claims = event["requestContext"]["authorizer"]["claims"]
        user_id = claims.get("sub")  # Cognito UUID
        email = claims.get("email", "")
        actor_id = claims.get("custom:actor_id", "")  # PH-* policyholder ID

        # Parse request body
        body = json.loads(event["body"]) if event.get("body") else {}

        # Generate session ID (timestamp-based for sort order)
        timestamp = datetime.now(timezone.utc)
        session_id = f"{timestamp.strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8]}"

        session_title = body.get("session_title", "New conversation")[:50]

        session_item = {
            "user_id": user_id,
            "session_id": session_id,
            "actor_id": actor_id,  # PH-* ID for AgentCore Memory namespacing
            "user_email": email,
            "session_title": session_title,
            "created_at": timestamp.isoformat(),
            "updated_at": timestamp.isoformat(),
        }

        table.put_item(Item=session_item)

        return {
            "statusCode": 201,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
                "Access-Control-Allow-Credentials": True,
            },
            "body": json.dumps({"message": "Session created", "session": session_item}),
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
