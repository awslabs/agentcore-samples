import json
import os
import uuid
from datetime import datetime, timezone

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("CLAIMS_TABLE", "ClaimsAgent-Claims"))


def handler(event, context):
    policy_number = event.get("policy_number", "")
    description = event.get("description", "")
    estimated_amount = float(event.get("estimated_amount", 0))
    category = event.get("category", "general")

    if not policy_number or not description:
        return json.dumps({"error": "policy_number and description are required"})

    if len(description) > 5000:
        return json.dumps({"error": "description exceeds 5000 character limit"})

    if estimated_amount < 0 or estimated_amount > 10_000_000:
        return json.dumps({"error": "estimated_amount must be between 0 and 10,000,000"})

    # Agent's dual-agent architecture determines routing; Lambda executes what the agent decides
    status = event.get("status", "pending_review")
    decision = event.get("decision", "agent_routed")

    claim_id = f"CLM-{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now(timezone.utc).isoformat()

    item = {
        "claim_id": claim_id,
        "policy_number": policy_number,
        "description": description,
        "estimated_amount": str(estimated_amount),
        "category": category,
        "status": status,
        "decision": decision,
        "created_at": timestamp,
    }
    table.put_item(Item=item)

    return json.dumps(item)
