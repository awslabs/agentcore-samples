import json
import logging
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
claims_table = dynamodb.Table(os.environ.get("CLAIMS_TABLE", "ClaimsAgent-Claims"))
reviews_table = dynamodb.Table(os.environ.get("REVIEWS_TABLE", "ClaimsAgent-Reviews"))


def handler(event, context):
    """Approve or deny a claim. Updates status and adds reviewer notes."""
    try:
        claim_id = event.get("claim_id", "")
        action = event.get("action", "").lower()
        reviewer_notes = event.get("reviewer_notes", "")

        if not claim_id:
            return json.dumps({"error": "claim_id is required"})
        if action not in ("approve", "deny"):
            return json.dumps({"error": "action must be 'approve' or 'deny'"})

        timestamp = datetime.now(timezone.utc).isoformat()
        new_status = "approved" if action == "approve" else "denied"

        claims_table.update_item(
            Key={"claim_id": claim_id},
            UpdateExpression="SET #s = :status, decision = :decision, reviewer_notes = :notes, resolved_at = :ts",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": new_status,
                ":decision": f"human_{action}d",
                ":notes": reviewer_notes,
                ":ts": timestamp,
            },
        )

        # Use claim-id-index GSI instead of scan to find related reviews
        reviews_resp = reviews_table.query(
            IndexName="claim-id-index",
            KeyConditionExpression=Key("claim_id").eq(claim_id),
        )
        for review in reviews_resp.get("Items", []):
            reviews_table.update_item(
                Key={"review_id": review["review_id"]},
                UpdateExpression="SET #s = :status, resolved_at = :ts",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":status": "resolved", ":ts": timestamp},
            )

        return json.dumps(
            {
                "claim_id": claim_id,
                "action": action,
                "new_status": new_status,
                "reviewer_notes": reviewer_notes,
                "resolved_at": timestamp,
            }
        )
    except Exception as e:
        logger.error("Failed to resolve claim", extra={"error": str(e), "claim_id": event.get("claim_id")})
        return json.dumps({"error": str(e)})
