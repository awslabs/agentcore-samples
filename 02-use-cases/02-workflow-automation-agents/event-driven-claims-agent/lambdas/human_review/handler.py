import json
import os
import uuid
from datetime import datetime, timezone

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("REVIEWS_TABLE", "ClaimsAgent-Reviews"))
sns = boto3.client("sns")
TOPIC_ARN = os.environ.get("REVIEW_SNS_TOPIC_ARN", "")


def handler(event, context):
    claim_id = event.get("claim_id", "")
    reason = event.get("reason", "")
    estimated_amount = float(event.get("estimated_amount", 0))

    if not claim_id:
        return json.dumps({"error": "claim_id is required"})

    review_id = f"REV-{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now(timezone.utc).isoformat()
    priority = "high" if estimated_amount >= 50000 else "medium"

    item = {
        "review_id": review_id,
        "claim_id": claim_id,
        "reason": reason,
        "estimated_amount": str(estimated_amount),
        "priority": priority,
        "status": "pending",
        "submitted_at": timestamp,
    }
    table.put_item(Item=item)

    if TOPIC_ARN:
        sns.publish(
            TopicArn=TOPIC_ARN,
            Subject=f"[{priority.upper()}] Review needed: {claim_id}",
            Message=json.dumps(item, indent=2),
        )

    return json.dumps(item)
