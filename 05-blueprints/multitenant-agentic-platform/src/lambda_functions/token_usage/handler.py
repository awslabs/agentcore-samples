import json
import os
import boto3
from boto3.dynamodb.conditions import Attr

dynamodb = boto3.resource("dynamodb")
aggregation_table = dynamodb.Table(os.environ["AGGREGATION_TABLE_NAME"])


def lambda_handler(event, context):
    try:
        # Scan the aggregation table with pagination and server-side filtering
        # Use FilterExpression to filter at DynamoDB level, not in memory
        tenant_items = []
        last_evaluated_key = None

        while True:
            scan_kwargs = {
                "FilterExpression": Attr("aggregation_key").begins_with("tenant:"),
                # Only retrieve fields needed by the frontend
                "ProjectionExpression": "aggregation_key, tenant_id, total_tokens, token_limit, #ts",
                "ExpressionAttributeNames": {"#ts": "timestamp"},  # 'timestamp' is a reserved word
            }

            if last_evaluated_key:
                scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

            response = aggregation_table.scan(**scan_kwargs)
            tenant_items.extend(response.get("Items", []))

            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
                "Access-Control-Allow-Methods": "GET,OPTIONS",
            },
            "body": json.dumps(tenant_items, default=str),
        }
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": str(e)}),
        }
