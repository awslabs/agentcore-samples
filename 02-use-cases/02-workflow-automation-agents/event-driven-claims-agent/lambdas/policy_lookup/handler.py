import json
import os

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("POLICIES_TABLE", "ClaimsAgent-Policies"))


def handler(event, context):
    policy_number = event.get("policy_number", "")
    if not policy_number:
        return json.dumps({"error": "policy_number is required"})

    resp = table.get_item(Key={"policy_number": policy_number})
    item = resp.get("Item")
    if not item:
        return json.dumps({"error": f"Policy {policy_number} not found"})

    return json.dumps(item, default=str)
