"""Gateway tool: get_user_profile — read a user's expense profile.

Receives a flat event dict (the MCP tool input) and returns a JSON string.
Reads only the userId it is handed — per-user data separation at the data layer
(spec §5.5).
"""

import json
import os

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("USERS_TABLE", "ReceiptsAgent-Users"))


def handler(event, context):
    user_id = event.get("user_id", "")
    if not user_id:
        return json.dumps({"error": "user_id is required"})

    item = table.get_item(Key={"userId": user_id}).get("Item")
    if not item:
        return json.dumps({"error": f"User {user_id} not found"})

    return json.dumps(item, default=str)
