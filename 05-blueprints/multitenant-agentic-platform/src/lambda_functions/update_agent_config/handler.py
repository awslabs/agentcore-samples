import json
import os
import boto3
import traceback
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
config_table = dynamodb.Table(os.environ["AGENT_CONFIG_TABLE_NAME"])

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "PUT,GET,OPTIONS",
}


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    try:
        # Handle GET request - retrieve config
        if event.get("httpMethod") == "GET":
            query_params = event.get("queryStringParameters") or {}
            tenant_id = query_params.get("tenantId")
            agent_runtime_id = query_params.get("agentRuntimeId")

            if not tenant_id or not agent_runtime_id:
                return {
                    "statusCode": 400,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": "Both tenantId and agentRuntimeId are required"}),
                }

            response = config_table.get_item(Key={"tenantId": tenant_id, "agentRuntimeId": agent_runtime_id})

            if "Item" in response:
                return {
                    "statusCode": 200,
                    "headers": CORS_HEADERS,
                    "body": json.dumps(response["Item"], default=str),
                }
            else:
                return {
                    "statusCode": 404,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": "Configuration not found"}),
                }

        # Handle PUT request - update config
        elif event.get("httpMethod") == "PUT":
            raw_body = event.get("body") or "{}"
            body = json.loads(raw_body)
            tenant_id = body.get("tenantId")
            agent_runtime_id = body.get("agentRuntimeId")
            config_updates = body.get("config", {})

            if not tenant_id or not agent_runtime_id:
                return {
                    "statusCode": 400,
                    "headers": CORS_HEADERS,
                    "body": json.dumps({"error": "Both tenantId and agentRuntimeId are required"}),
                }

            # Build update expression
            update_expr_parts = []
            expr_attr_values = {}
            expr_attr_names = {}

            # Always update the timestamp
            update_expr_parts.append("#attr0 = :val0")
            expr_attr_names["#attr0"] = "updatedAt"
            expr_attr_values[":val0"] = datetime.now().isoformat()

            # Add config updates using indexed placeholders
            for idx, (key, value) in enumerate(config_updates.items(), start=1):
                attr_placeholder = f"#attr{idx}"
                value_placeholder = f":val{idx}"
                update_expr_parts.append(f"{attr_placeholder} = {value_placeholder}")
                expr_attr_names[attr_placeholder] = key
                expr_attr_values[value_placeholder] = value

            update_expression = "SET " + ", ".join(update_expr_parts)

            print(f"Updating config for tenant {tenant_id}, agent {agent_runtime_id}")
            print(f"Update expression: {update_expression}")
            print(f"Values: {config_updates}")

            response = config_table.update_item(
                Key={"tenantId": tenant_id, "agentRuntimeId": agent_runtime_id},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expr_attr_names,
                ExpressionAttributeValues=expr_attr_values,
                ReturnValues="ALL_NEW",
            )

            return {
                "statusCode": 200,
                "headers": CORS_HEADERS,
                "body": json.dumps(
                    {
                        "message": "Configuration updated successfully",
                        "config": response["Attributes"],
                    },
                    default=str,
                ),
            }

        else:
            return {
                "statusCode": 405,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "Method not allowed"}),
            }

    except Exception as e:
        print(f"Error: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": str(e)}),
        }
