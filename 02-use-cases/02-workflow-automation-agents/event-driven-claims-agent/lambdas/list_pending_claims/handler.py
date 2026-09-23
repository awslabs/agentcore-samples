import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("CLAIMS_TABLE", "ClaimsAgent-Claims"))


def handler(event, context):
    """List all claims with pending_review status using GSI (avoids full table scan)."""
    try:
        # Use status-index GSI for efficient query instead of scan
        response = table.query(
            IndexName="status-index",
            KeyConditionExpression=Key("status").eq("pending_review"),
        )
        claims = response.get("Items", [])
        if not claims:
            return json.dumps({"message": "No pending claims to review.", "claims": []})
        result = []
        for c in claims:
            result.append(
                {
                    "claim_id": c.get("claim_id"),
                    "policy_number": c.get("policy_number"),
                    "description": c.get("description"),
                    "estimated_amount": c.get("estimated_amount"),
                    "category": c.get("category"),
                    "created_at": c.get("created_at"),
                }
            )
        return json.dumps({"message": f"Found {len(result)} pending claim(s).", "claims": result})
    except Exception as e:
        logger.error("Failed to list pending claims", extra={"error": str(e)})
        return json.dumps({"error": str(e)})
