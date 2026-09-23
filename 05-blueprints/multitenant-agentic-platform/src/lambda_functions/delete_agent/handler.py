import json
import os
import boto3
import traceback

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["AGENT_DETAILS_TABLE_NAME"])
# Use bedrock-agentcore-control for control plane operations (create/delete)
bedrock_control_client = boto3.client("bedrock-agentcore-control", region_name=os.environ["AWS_REGION"])

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "DELETE,OPTIONS",
}


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    try:
        # Get tenantId and agentRuntimeId from query parameters
        query_params = event.get("queryStringParameters") or {}
        tenant_id = query_params.get("tenantId")
        agent_runtime_id = query_params.get("agentRuntimeId")

        if not tenant_id or not agent_runtime_id:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "Both tenantId and agentRuntimeId query parameters are required"}),
            }

        # Get agent details from DynamoDB using composite key
        print(f"Looking for agent with tenantId: {tenant_id}, agentRuntimeId: {agent_runtime_id}")
        response = table.get_item(Key={"tenantId": tenant_id, "agentRuntimeId": agent_runtime_id})

        if "Item" not in response:
            return {
                "statusCode": 404,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "Agent not found"}),
            }

        # Step 1: Delete the agent runtime from Bedrock first
        print(f"Step 1: Deleting agent runtime from Bedrock: {agent_runtime_id}")
        try:
            # Use the control plane client to delete the agent runtime
            response = bedrock_control_client.delete_agent_runtime(agentRuntimeId=agent_runtime_id)
            print(f"Successfully deleted agent runtime from Bedrock: {agent_runtime_id}")
            print(f"Delete response: {response}")
        except Exception as bedrock_error:
            error_msg = str(bedrock_error)
            error_type = type(bedrock_error).__name__
            print(f"Error deleting from Bedrock ({error_type}): {error_msg}")
            print(f"Traceback: {traceback.format_exc()}")

            # Check if it's just a "not found" error (agent already deleted)
            if "ResourceNotFoundException" in error_type or "NotFound" in error_msg or "404" in error_msg:
                print("Agent runtime not found in Bedrock (may have been already deleted)")
                # Continue to delete from DB
            else:
                # Real error - don't delete from DB
                return {
                    "statusCode": 500,
                    "headers": CORS_HEADERS,
                    "body": json.dumps(
                        {
                            "error": "Failed to delete agent from Bedrock",
                            "details": error_msg,
                            "errorType": error_type,
                            "tenantId": tenant_id,
                            "agentRuntimeId": agent_runtime_id,
                        }
                    ),
                }

        # Step 2: Only delete from DynamoDB if Bedrock deletion succeeded
        print(
            f"Step 2: Deleting agent details from DynamoDB for tenantId: {tenant_id}, agentRuntimeId: {agent_runtime_id}"
        )
        try:
            table.delete_item(Key={"tenantId": tenant_id, "agentRuntimeId": agent_runtime_id})
            print("Successfully deleted agent details from DynamoDB")
        except Exception as db_error:
            error_msg = str(db_error)
            print(  # nosec B608
                f"Warning: Agent deleted from Bedrock but failed to delete from DynamoDB: {error_msg}"
            )
            # Agent is deleted from Bedrock but not from DB - return partial success
            return {
                "statusCode": 207,  # Multi-Status
                "headers": CORS_HEADERS,
                "body": json.dumps(
                    {
                        "message": "Agent deleted from Bedrock but failed to remove from database",
                        "warning": "Database entry still exists",
                        "details": error_msg,
                        "tenantId": tenant_id,
                        "agentRuntimeId": agent_runtime_id,
                    }
                ),
            }

        # Step 3: Return success response
        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps(
                {
                    "message": "Agent deleted successfully from both Bedrock and database",
                    "tenantId": tenant_id,
                    "agentRuntimeId": agent_runtime_id,
                }
            ),
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": str(e)}),
        }
