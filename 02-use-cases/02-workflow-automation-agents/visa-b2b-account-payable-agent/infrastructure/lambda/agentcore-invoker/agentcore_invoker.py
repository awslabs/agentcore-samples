"""
AgentCore Runtime Invoker Lambda

This Lambda acts as a bridge between TypeScript backend and AgentCore Runtime.
It uses boto3 to invoke the Bedrock AgentCore Runtime directly.

Version: 1.1 - Removed unnecessary dependencies
"""

import json
import os
import boto3

# Environment variables
RUNTIME_ARN = os.environ.get("RUNTIME_ARN")
REGION = os.environ.get("AWS_REGION", "us-east-1")

# Initialize boto3 client for bedrock-agentcore
bedrock_agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)


def lambda_handler(event, context):
    """
    Lambda handler for invoking AgentCore Runtime

    Expected event format:
    {
        "payload": {
            "invoice_id": "uuid",
            "action": "process_payment"
        }
    }

    Returns:
    {
        "statusCode": 200,
        "body": {
            "status": "success",
            "response": {...}
        }
    }
    """
    try:
        print("AgentCore Invoker Lambda started")
        print(f"Event: {json.dumps(event)}")

        # Extract payload
        payload = event.get("payload", {})

        if not payload:
            return {
                "statusCode": 400,
                "body": json.dumps({"status": "error", "error": "Missing payload"}),
            }

        print(f"Invoking AgentCore Runtime: {RUNTIME_ARN}")
        print(f"Payload: {json.dumps(payload)}")

        # Invoke the runtime using boto3 client (correct API from samples)
        response = bedrock_agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=RUNTIME_ARN,
            qualifier="DEFAULT",
            payload=json.dumps(payload),
        )

        # Parse the response (handle streaming or direct response)
        if "text/event-stream" in response.get("contentType", ""):
            # Streaming response
            result = ""
            for line in response["response"].iter_lines(chunk_size=1):
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data:"):
                        line = line[6:]
                    if line.startswith('"') and line.endswith('"'):
                        line = line[1:-1]
                    line = line.replace("\\n", "\n")
                    result += line
            response_body = json.loads(result) if result else {}
        else:
            # Direct response
            response_text = response["response"].read()
            response_body = json.loads(response_text)

        print(f"AgentCore Runtime response: {json.dumps(response_body)}")

        return {
            "statusCode": 200,
            "body": json.dumps({"status": "success", "response": response_body}),
        }

    except Exception as e:
        print(f"Error invoking AgentCore Runtime: {str(e)}")
        import traceback

        traceback.print_exc()

        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "error": str(e)}),
        }
