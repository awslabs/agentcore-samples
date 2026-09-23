"""
Lambda function that invokes an AgentCore Runtime agent.

Environment variables:
  RUNTIME_ARN  - AgentCore Runtime ARN (set by deploy.py)

Expected event format:
  {"prompt": "Your question here", "sessionId": "optional-session-id"}
"""

import json
import os
import traceback

import boto3
from botocore.exceptions import ClientError


def lambda_handler(event, context):
    bedrock_agentcore_client = boto3.client("bedrock-agentcore")

    try:
        runtime_arn = os.environ.get("RUNTIME_ARN")
        if not runtime_arn:
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Missing RUNTIME_ARN environment variable"}),
            }

        if isinstance(event, str):
            event = json.loads(event)

        prompt = event.get("prompt", "")
        session_id = event.get("sessionId", context.aws_request_id)

        if not prompt:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing prompt in request"}),
            }

        print(f"Prompt: {prompt}")
        print(f"Session ID: {session_id}")

        response = bedrock_agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": prompt}),
        )

        # Parse StreamingBody response
        agent_response = ""
        response_body = response.get("response")
        if response_body is not None:
            if hasattr(response_body, "read"):
                raw = response_body.read()
                agent_response = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            elif isinstance(response_body, bytes):
                agent_response = response_body.decode("utf-8")
            elif isinstance(response_body, list) and response_body:
                item = response_body[0]
                agent_response = item.decode("utf-8") if isinstance(item, bytes) else str(item)
            else:
                agent_response = str(response_body)

        if not agent_response:
            agent_response = "No response from agent"

        return {
            "statusCode": 200,
            "body": json.dumps({"response": agent_response, "sessionId": session_id}),
            "headers": {"Content-Type": "application/json"},
        }

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_message = e.response["Error"]["Message"]
        print(f"ClientError {error_code}: {error_message}")
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_code, "message": error_message}),
        }

    except Exception as e:
        print(f"Unexpected error: {e}")
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "InternalError", "message": str(e)}),
        }
