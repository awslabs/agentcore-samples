import json
import os
import boto3
from decimal import Decimal

# Lazy initialization to support testing
_dynamodb = None
_aggregation_table = None


def get_aggregation_table():
    """Get DynamoDB table with lazy initialization."""
    global _dynamodb, _aggregation_table
    if _aggregation_table is None:
        _dynamodb = boto3.resource("dynamodb")
        _aggregation_table = _dynamodb.Table(os.environ["AGGREGATION_TABLE_NAME"])
    return _aggregation_table


CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


def validate_token_limit(value):
    """
    Validate that token limit is a positive integer greater than zero.
    Returns (is_valid, error_message)
    """
    if value is None:
        return False, "Token limit is required"

    if isinstance(value, bool):
        return False, "Token limit must be a positive integer"

    if isinstance(value, float) and not value.is_integer():
        return False, "Token limit must be a positive integer, not a decimal"

    try:
        int_value = int(value)
        if int_value <= 0:
            return False, "Token limit must be greater than zero"
        return True, None
    except (ValueError, TypeError):
        return False, "Token limit must be a positive integer"


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    try:
        # Parse request body
        raw_body = event.get("body")
        if raw_body:
            body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
        else:
            body = {}

        tenant_id = body.get("tenantId")
        token_limit = body.get("tokenLimit")

        # Validate tenant ID
        if not tenant_id:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "tenantId is required"}),
            }

        # Validate token limit
        is_valid, error_message = validate_token_limit(token_limit)
        if not is_valid:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": error_message}),
            }

        token_limit_int = int(token_limit)
        aggregation_key = f"tenant:{tenant_id}"

        # Update or create the tenant record with token_limit
        aggregation_table = get_aggregation_table()
        aggregation_table.update_item(
            Key={"aggregation_key": aggregation_key},
            UpdateExpression="SET token_limit = :limit, tenant_id = :tenant_id",
            ExpressionAttributeValues={
                ":limit": Decimal(token_limit_int),
                ":tenant_id": tenant_id,
            },
            ReturnValues="ALL_NEW",
        )

        print(f"Updated tenant {tenant_id} with token_limit: {token_limit_int}")

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({"success": True, "tenantId": tenant_id, "tokenLimit": token_limit_int}),
        }

    except Exception as e:
        print(f"Error setting token limit: {str(e)}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": str(e)}),
        }
