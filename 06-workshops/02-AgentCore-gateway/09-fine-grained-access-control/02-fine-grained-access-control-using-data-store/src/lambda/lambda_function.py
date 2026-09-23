"""
Lambda Interceptor with DynamoDB Tool Permission Filtering

This Lambda function intercepts Gateway MCP RESPONSES and filters tools based on
client permissions stored in DynamoDB. It is configured as a RESPONSE interceptor
that filters the tools/list response. Only tools that the client is authorized to
access will be returned.

The interceptor extracts client_id from JWT token in Authorization header.
"""

import json
import boto3
import os
import base64
from typing import List, Dict, Any, Optional
from botocore.exceptions import ClientError

# Environment variables (set during deployment)
TABLE_NAME = os.environ.get("PERMISSIONS_TABLE_NAME", "ClientToolPermissions")
REGION = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))

# Initialize DynamoDB resource
dynamodb = boto3.resource("dynamodb", region_name=REGION)
permissions_table = dynamodb.Table(TABLE_NAME)


def extract_client_id_from_jwt(token: str) -> Optional[str]:
    """
    Extract client_id from JWT token payload.

    Args:
        token: JWT token string (with or without 'Bearer ' prefix)

    Returns:
        client_id from token payload, or None if extraction fails
    """
    try:
        # Remove 'Bearer ' prefix if present
        if token.startswith("Bearer "):
            token = token[7:]

        # Split token into parts
        parts = token.split(".")
        if len(parts) != 3:
            print(f"Invalid JWT format: expected 3 parts, got {len(parts)}")
            return None

        # Decode payload (second part)
        payload = parts[1]

        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4)

        # Decode base64
        decoded = base64.urlsafe_b64decode(payload)
        payload_data = json.loads(decoded)

        # Extract client_id (don't log full payload - may contain sensitive data)
        client_id = payload_data.get("client_id")

        if client_id:
            print("Successfully extracted client_id from JWT")
        else:
            print("WARNING: No client_id found in JWT payload")

        return client_id

    except Exception as e:
        print(f"Error extracting client_id from JWT: {e}")
        return None


def get_client_permissions(client_id: str) -> List[str]:
    """
    Query DynamoDB to get all allowed tools for a specific client.

    Args:
        client_id: The client ID to look up

    Returns:
        List of tool names that the client is allowed to access
    """
    try:
        print(f"Querying permissions for client: {client_id}")

        response = permissions_table.query(
            KeyConditionExpression="ClientID = :client_id",
            ExpressionAttributeValues={":client_id": client_id},
        )

        # Filter for only allowed tools
        allowed_tools = [item["ToolName"] for item in response.get("Items", []) if item.get("Allowed", False)]

        print(f"Found {len(allowed_tools)} allowed tools for client {client_id}: {allowed_tools}")
        return allowed_tools

    except ClientError as e:
        print(f"Error querying DynamoDB: {e}")
        print(f"Error details: {e.response}")
        # On error, return empty list (deny all tools)
        return []
    except Exception as e:
        print(f"Unexpected error getting permissions: {e}")
        return []


def extract_tool_name(gateway_tool_name: str) -> str:
    """
    Extract actual tool name from Gateway's naming format.
    Gateway returns: 'target-name___tool_name'
    We need: 'tool_name'

    Args:
        gateway_tool_name: Tool name in Gateway format

    Returns:
        Extracted tool name
    """
    if "___" in gateway_tool_name:
        return gateway_tool_name.split("___")[1]
    return gateway_tool_name


def filter_tools(tools: List[Dict[str, Any]], allowed_tools: List[str]) -> List[Dict[str, Any]]:
    """
    Filter tools list to only include tools the client is allowed to access.
    Handles Gateway's 'target-name___tool_name' naming format.

    Args:
        tools: List of tool dictionaries from Gateway
        allowed_tools: List of allowed tool names from DynamoDB

    Returns:
        Filtered list of tools
    """
    if not tools:
        return []

    # Convert allowed_tools to set for faster lookup
    allowed_set = set(allowed_tools)

    filtered = []
    for tool in tools:
        gateway_name = tool.get("name", "")
        extracted_name = extract_tool_name(gateway_name)

        if extracted_name in allowed_set:
            filtered.append(tool)

    print(f"Filtered {len(tools)} tools down to {len(filtered)} allowed tools")

    # Log which tools were filtered out
    filtered_out = []
    for tool in tools:
        gateway_name = tool.get("name", "")
        extracted_name = extract_tool_name(gateway_name)
        if extracted_name not in allowed_set:
            filtered_out.append(gateway_name)

    if filtered_out:
        print(f"Filtered out tools: {filtered_out}")

    return filtered


def lambda_handler(event, context):
    """
    Main Lambda handler for Gateway RESPONSE interceptor.

    Expected event structure (from Gateway RESPONSE):
    {
        "mcp": {
            "gatewayResponse": {
                "headers": {
                    "content-type": "application/json",
                    ...
                },
                "body": {
                    "jsonrpc": "2.0",
                    "result": {
                        "tools": [...]  # Tools list from Gateway targets
                    },
                    "id": 1
                }
            },
            "gatewayRequest": {
                "headers": {
                    "authorization": "Bearer <JWT_TOKEN>",
                    ...
                }
            }
        }
    }

    Returns transformed response with filtered tools.
    """
    print(f"Received event: {json.dumps(event, default=str)}")

    try:
        # Extract both request (for Authorization header) and response (for tools)
        mcp_data = event.get("mcp", {})
        gateway_response = mcp_data.get("gatewayResponse", {})
        gateway_request = mcp_data.get("gatewayRequest", {})

        # Get request headers for Authorization
        request_headers = gateway_request.get("headers", {})

        # Get response data
        response_headers = gateway_response.get("headers", {})
        response_body = gateway_response.get("body", {})

        # Extract Authorization header (case-insensitive lookup)
        auth_header = None
        for key, value in request_headers.items():
            if key.lower() == "authorization":
                auth_header = value
                break

        print(f"Authorization header present: {bool(auth_header)}")

        # Extract client_id from JWT token
        client_id = None
        if auth_header:
            client_id = extract_client_id_from_jwt(auth_header)

        print(f"Extracted client_id: {client_id}")

        # If no client_id extracted, deny all tools (security: fail closed)
        if not client_id:
            print("ERROR: No client_id found in JWT token, denying all tools")
            # Try to preserve the original response structure but with empty tools
            denied_body = {
                "jsonrpc": "2.0",
                "result": {
                    "tools": []  # Deny all tools when client_id is missing
                },
            }
            # Preserve the id field if it exists in the original response
            if isinstance(response_body, dict) and "id" in response_body:
                denied_body["id"] = response_body["id"]

            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayResponse": {
                        "headers": {
                            "Content-Type": "application/json",
                            "X-Auth-Error": "MissingClientId",
                        },
                        "body": denied_body,
                    }
                },
            }

        # Get allowed tools for this client from DynamoDB
        allowed_tools = get_client_permissions(client_id)

        # Check if this is a tools/list response (MCP JSON-RPC format)
        # Response body format: {"jsonrpc": "2.0", "result": {"tools": [...]}, "id": 1}
        if "result" in response_body and "tools" in response_body.get("result", {}):
            result = response_body["result"]
            original_tools = result.get("tools", [])

            # Filter tools based on permissions
            filtered_tools = filter_tools(original_tools, allowed_tools)

            # Update response with filtered tools
            filtered_body = response_body.copy()
            filtered_body["result"] = result.copy()
            filtered_body["result"]["tools"] = filtered_tools

            # Log permission enforcement
            print("Permission enforcement summary:")
            print(f"  - Client ID: {client_id}")
            print(f"  - Original tools count: {len(original_tools)}")
            print(f"  - Filtered tools count: {len(filtered_tools)}")
            print(f"  - Tools removed: {len(original_tools) - len(filtered_tools)}")

            # Return transformed response with filtered tools
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayResponse": {
                        "headers": response_headers,
                        "body": filtered_body,
                    }
                },
            }
        else:
            # Not a tools/list response, pass through unchanged
            print("Not a tools/list response, passing through unchanged")
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayResponse": {
                        "headers": response_headers,
                        "body": response_body,
                    }
                },
            }

    except Exception as e:
        print(f"ERROR in lambda_handler: {e}")
        print(f"Exception type: {type(e).__name__}")

        import traceback

        print(f"Traceback: {traceback.format_exc()}")

        # On error, return minimal safe response (no tools)
        error_response = {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayResponse": {
                    "headers": {
                        "Content-Type": "application/json",
                        "X-Error": "InterceptorError",
                    },
                    "body": {
                        "jsonrpc": "2.0",
                        "result": {
                            "tools": []  # Safe default: no tools on error
                        },
                        "id": 1,
                    },
                }
            },
        }

        return error_response
