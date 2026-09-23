import json
import os
import boto3
from boto3.dynamodb.conditions import Attr
import traceback

bedrock_runtime = boto3.client("bedrock-agentcore", region_name=os.environ["AWS_REGION"])

# Lazy initialization for DynamoDB (only needed if AGGREGATION_TABLE_NAME is set)
_dynamodb = None
_aggregation_table = None


def get_aggregation_table():
    """Get DynamoDB aggregation table with lazy initialization."""
    global _dynamodb, _aggregation_table
    if _aggregation_table is None:
        table_name = os.environ.get("AGGREGATION_TABLE_NAME")
        if table_name:
            _dynamodb = boto3.resource("dynamodb")
            _aggregation_table = _dynamodb.Table(table_name)
    return _aggregation_table


def check_token_limit(tenant_id: str) -> tuple:
    """
    Check if tenant has exceeded their token limit.

    Returns:
        tuple: (allowed: bool, usage_info: dict)
        - allowed: True if request can proceed, False if limit exceeded
        - usage_info: Contains current usage and limit for error message

    Note: This function fails open by default (allows requests on errors).
    Set FAIL_CLOSED=true environment variable to fail closed instead.
    """
    table = get_aggregation_table()
    if table is None:
        # No aggregation table configured, allow request
        return True, {}

    try:
        aggregation_key = f"tenant:{tenant_id}"
        response = table.get_item(Key={"aggregation_key": aggregation_key})

        item = response.get("Item")
        if not item:
            # No usage record for tenant, allow request
            return True, {}

        token_limit = item.get("token_limit")
        if token_limit is None:
            # No limit set for tenant, allow request
            return True, {"total_tokens": int(item.get("total_tokens", 0))}

        total_tokens = int(item.get("total_tokens", 0))
        token_limit = int(token_limit)

        usage_info = {
            "tenant_id": tenant_id,
            "total_tokens": total_tokens,
            "token_limit": token_limit,
        }

        if total_tokens >= token_limit:
            # Limit exceeded
            return False, usage_info

        return True, usage_info

    except Exception as e:
        error_msg = f"Error checking token limit for tenant {tenant_id}: {str(e)}"
        print(error_msg)

        # Check if we should fail closed (deny on error) or fail open (allow on error)
        fail_closed = os.environ.get("FAIL_CLOSED", "false").lower() == "true"

        if fail_closed:
            # Fail closed: deny request on error
            print("FAIL_CLOSED mode: Denying request due to error checking token limit")
            return False, {
                "error": "Unable to verify token limit",
                "tenant_id": tenant_id,
                "message": "Service temporarily unavailable. Please try again later.",
            }
        else:
            # Fail open: allow request on error (default behavior)
            print("FAIL_OPEN mode: Allowing request despite error checking token limit")
            return True, {}


def get_tenant_id_from_agent(agent_runtime_arn: str) -> str:
    """
    Look up the tenant ID associated with an agent from the agent details table.
    This prevents clients from bypassing token limits by omitting or spoofing tenantId.

    Args:
        agent_runtime_arn: The agent runtime ARN

    Returns:
        str: The tenant ID associated with the agent, or None if not found
    """
    # Get agent details table name from environment
    agent_details_table_name = os.environ.get("AGENT_DETAILS_TABLE_NAME")
    if not agent_details_table_name:
        print("WARNING: AGENT_DETAILS_TABLE_NAME not configured, cannot look up tenant ID")
        return None

    try:
        dynamodb = boto3.resource("dynamodb")
        agent_details_table = dynamodb.Table(agent_details_table_name)

        # Scan for the agent with this ARN
        # Note: This could be optimized with a GSI on agentRuntimeArn
        response = agent_details_table.scan(
            FilterExpression=Attr("agentRuntimeArn").eq(agent_runtime_arn),
            ProjectionExpression="tenantId",
        )

        items = response.get("Items", [])
        if items:
            tenant_id = items[0].get("tenantId")
            print(f"Found tenant ID {tenant_id} for agent {agent_runtime_arn}")
            return tenant_id
        else:
            print(f"WARNING: No tenant found for agent {agent_runtime_arn}")
            return None

    except Exception as e:
        print(f"ERROR: Failed to look up tenant ID for agent {agent_runtime_arn}: {str(e)}")
        return None


def get_tenant_id_from_agent(agent_runtime_arn: str) -> str:  # noqa: F811
    """
    Look up the tenant ID associated with an agent from the agent details table.
    This prevents clients from bypassing token limits by omitting or spoofing tenant IDs.

    Args:
        agent_runtime_arn: The agent runtime ARN

    Returns:
        The tenant ID associated with the agent, or None if not found
    """
    try:
        # Get agent details table name from environment
        agent_details_table_name = os.environ.get("AGENT_DETAILS_TABLE_NAME")
        if not agent_details_table_name:
            print("WARNING: AGENT_DETAILS_TABLE_NAME not configured, cannot look up tenant ID")
            return None

        dynamodb = boto3.resource("dynamodb")
        agent_details_table = dynamodb.Table(agent_details_table_name)

        # Extract agent runtime ID from ARN
        # ARN format: arn:aws:bedrock-agentcore:region:account:agent/agent-runtime-id
        agent_runtime_id = agent_runtime_arn.split("/")[-1] if "/" in agent_runtime_arn else agent_runtime_arn

        # Scan for the agent (we need to find by agentRuntimeId which is the sort key)
        # In production, consider adding a GSI on agentRuntimeId for better performance
        response = agent_details_table.scan(
            FilterExpression=Attr("agentRuntimeId").eq(agent_runtime_id)
            | Attr("agentRuntimeArn").eq(agent_runtime_arn),
            ProjectionExpression="tenantId",
        )

        items = response.get("Items", [])
        if items:
            tenant_id = items[0].get("tenantId")
            print(f"Found tenant ID {tenant_id} for agent {agent_runtime_id}")
            return tenant_id
        else:
            print(f"WARNING: No tenant found for agent {agent_runtime_id}")
            return None

    except Exception as e:
        print(f"Error looking up tenant ID for agent {agent_runtime_arn}: {str(e)}")
        return None


def extract_tenant_from_agent_arn(agent_arn: str) -> str:
    """
    Extract tenant ID from agent ARN or return a default.
    Agent ARN format varies, so we need to look up the tenant from agent details.
    For now, we'll use a query parameter or body field.
    """
    # This is a placeholder - in production, you'd look up the tenant from agent details
    return None


# CORS headers for all responses
CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


def extract_text_from_response(obj):
    """
    Recursively extract text content from nested response structure.
    Handles structures like: {'result': {'role': 'assistant', 'content': [{'text': '...'}]}}
    """
    if isinstance(obj, str):
        return obj

    if isinstance(obj, dict):
        # Check for result key
        if "result" in obj:
            return extract_text_from_response(obj["result"])

        # Check for role and content (Anthropic format)
        if "role" in obj and "content" in obj:
            return extract_text_from_response(obj["content"])

        # Check for content array
        if "content" in obj and isinstance(obj["content"], list):
            texts = []
            for item in obj["content"]:
                if isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
                elif isinstance(item, str):
                    texts.append(item)
            return "\n\n".join(texts) if texts else str(obj)

        # Check for direct text field
        if "text" in obj:
            return obj["text"]

        # Check for message or completion
        if "message" in obj:
            return extract_text_from_response(obj["message"])
        if "completion" in obj:
            return extract_text_from_response(obj["completion"])

    if isinstance(obj, list):
        texts = []
        for item in obj:
            if isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
            elif isinstance(item, str):
                texts.append(item)
            else:
                texts.append(extract_text_from_response(item))
        return "\n\n".join(texts) if texts else str(obj)

    return str(obj)


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")

    try:
        raw_body = event.get("body") or "{}"
        body = json.loads(raw_body)
        agent_id = body.get("agentId")
        input_text = body.get("inputText")
        session_id = body.get("sessionId", "default-session")

        print(f"Agent ID: {agent_id}")
        print(f"Input text: {input_text}")

        if not agent_id or not input_text:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "agentId and inputText are required"}),
            }

        # Look up tenant ID from agent details (server-side, not client-supplied)
        # This prevents clients from bypassing token limits
        tenant_id = get_tenant_id_from_agent(agent_id)

        if tenant_id:
            print(f"Tenant ID (server-side lookup): {tenant_id}")
            allowed, usage_info = check_token_limit(tenant_id)
            if not allowed:
                # Check if this is a fail-closed error (service unavailable) or actual limit exceeded
                if "error" in usage_info:
                    # Fail-closed: service error, return 503
                    print(f"Service error checking token limit for tenant {tenant_id}: {usage_info}")
                    return {
                        "statusCode": 503,
                        "headers": CORS_HEADERS,
                        "body": json.dumps(
                            {
                                "error": usage_info.get("error", "Service unavailable"),
                                "message": usage_info.get("message", "Unable to process request at this time."),
                                "tenant_id": tenant_id,
                            }
                        ),
                    }
                else:
                    # Token limit exceeded, return 429
                    print(f"Token limit exceeded for tenant {tenant_id}: {usage_info}")
                    return {
                        "statusCode": 429,
                        "headers": CORS_HEADERS,
                        "body": json.dumps(
                            {
                                "error": "Token limit exceeded",
                                "message": f"Tenant {tenant_id} has reached their token limit of {usage_info.get('token_limit', 0):,} tokens. Current usage: {usage_info.get('total_tokens', 0):,} tokens.",
                                "tenant_id": tenant_id,
                                "token_limit": usage_info.get("token_limit"),
                                "current_usage": usage_info.get("total_tokens"),
                            }
                        ),
                    }

        # Invoke the agent
        print(f"Invoking agent: {agent_id}")
        response = bedrock_runtime.invoke_agent_runtime(
            agentRuntimeArn=agent_id,
            payload=json.dumps({"message": input_text}).encode("utf-8"),
            contentType="application/json",
        )

        # Log the full response structure for debugging
        print(f"Full response keys: {list(response.keys())}")
        print(f"Response metadata: {response.get('ResponseMetadata', {})}")

        # The actual response is in the 'response' key, not 'body'
        response_data = response.get("response", "")

        # Handle different response types
        if hasattr(response_data, "read"):
            # It's a StreamingBody
            response_data = response_data.read()

        # Decode if bytes
        if isinstance(response_data, bytes):
            response_data = response_data.decode("utf-8")

        print(f"Agent response data: {response_data}")
        print(f"Agent response data type: {type(response_data)}")
        print(f"Agent response data length: {len(str(response_data))}")

        # Try to parse as JSON if it's a string
        try:
            if isinstance(response_data, str) and response_data.strip():
                parsed_response = json.loads(response_data)
                # Extract the actual text from the nested structure
                response_body = extract_text_from_response(parsed_response)
            else:
                response_body = response_data
        except json.JSONDecodeError:
            # If not JSON, use as-is
            response_body = response_data

        # If response is empty, return a message indicating the agent processed the request
        if not response_body or str(response_body).strip() == "":
            response_body = "Agent processed the request successfully. Check token usage for confirmation."

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({"completion": response_body, "sessionId": session_id}),
        }
    except Exception as e:
        error_msg = str(e)
        error_trace = traceback.format_exc()
        print(f"Error invoking agent: {error_msg}")
        print(f"Traceback: {error_trace}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": error_msg, "type": type(e).__name__}),
        }
