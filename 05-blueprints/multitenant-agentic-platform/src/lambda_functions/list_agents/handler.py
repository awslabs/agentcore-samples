import json
import os
import boto3
import traceback

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["AGENT_DETAILS_TABLE_NAME"])

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    try:
        # Scan DynamoDB for all agents with pagination
        agents = []
        last_evaluated_key = None

        while True:
            if last_evaluated_key:
                response = table.scan(ExclusiveStartKey=last_evaluated_key)
            else:
                response = table.scan()

            agents.extend(response.get("Items", []))

            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

        print(f"Found {len(agents)} agents")

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps(agents, default=str),
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": str(e)}),
        }
