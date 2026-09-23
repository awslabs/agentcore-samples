import json
import base64


def get_user_groups(jwt_token):
    """Extract user groups from JWT token.

    Args:
        jwt_token: JWT token string (with or without 'Bearer ' prefix)

    Returns:
        list: User groups (e.g., ['sre'] or ['approvers'])
    """
    try:
        # Remove 'Bearer ' prefix if present
        token = jwt_token.replace("Bearer ", "").strip()

        # JWT format: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return []

        # Decode payload (add padding if needed)
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding

        decoded = base64.urlsafe_b64decode(payload)
        claims = json.loads(decoded)

        # Extract cognito:groups claim
        groups = claims.get("cognito:groups", [])
        return groups
    except Exception as e:
        print(f"Error extracting groups from JWT: {e}")
        return []


def lambda_handler(event, context):
    try:
        print("=" * 80)
        print("INTERCEPTOR LAMBDA - FULL REQUEST DUMP")
        print("=" * 80)
        print(json.dumps(event, indent=2))
        print("=" * 80)

        # Extract the gateway request from the correct structure
        mcp_data = event.get("mcp", {})
        gateway_request = mcp_data.get("gatewayRequest", {})
        headers = gateway_request.get("headers", {})
        body = gateway_request.get("body", {})

        # Parse body as JSON with error handling
        try:
            body_json = json.loads(body) if isinstance(body, str) else body
        except json.JSONDecodeError as e:
            print(f"Error parsing body JSON: {e}")
            return _deny_request(None, message="Invalid JSON in request body")

        # Extract Authorization header
        auth_header = headers.get("authorization", "") or headers.get("Authorization", "")
        print(f"Authorization header received: {auth_header[:50]}..." if auth_header else "No Authorization header")

        # Extract user groups from JWT
        user_groups = get_user_groups(auth_header)
        print(f"User groups: {user_groups}")

        # Extract JSON-RPC method and id
        method = body_json.get("method")
        rpc_id = body_json.get("id")

        # Always pass through for non-tool calls (e.g., initialize, health checks)
        if method not in ("tools/call", "tools/list"):
            print(f"Non-tool method '{method}', passing through")
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayRequest": {
                        "headers": {
                            "Authorization": headers.get("Authorization", ""),
                            "Content-Type": "application/json",
                            "AgentID": headers.get("AgentID", ""),
                        },
                        "body": body_json,
                    }
                },
            }

        # tools/list is typically allowed without AgentID
        if method == "tools/list":
            print("Allowing tools/list")
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayRequest": {
                        "headers": {
                            "Authorization": headers.get("Authorization", ""),
                            "Content-Type": "application/json",
                        },
                        "body": body_json,
                    }
                },
            }

        # For tools/call, check authorization based on user groups
        if method == "tools/call":
            try:
                # Extract tool name and arguments from params
                tool_name = body_json.get("params", {}).get("name", "")
                tool_arguments = body_json.get("params", {}).get("arguments", {})
                print(f"Tool call requested: {tool_name}")

                # Check authorization
                if "sre" in user_groups:
                    # SRE can only use action_type="only_plan"
                    action_type = tool_arguments.get("action_type", "")
                    if action_type != "only_plan":
                        print(f"SRE user not authorized for action_type: {action_type}")
                        return _deny_request(
                            rpc_id,
                            message="SRE users can only use action_type='only_plan'",
                        )
                    print("SRE user authorized with action_type=only_plan")
                elif "approvers" in user_groups:
                    # Approvers can call all tools
                    print(f"Approver authorized for tool: {tool_name}")
                else:
                    print(f"User has no recognized groups: {user_groups}")
                    return _deny_request(
                        rpc_id,
                        message="User does not belong to authorized groups (sre or approvers)",
                    )

                # Pass through if authorized
                return {
                    "interceptorOutputVersion": "1.0",
                    "mcp": {
                        "transformedGatewayRequest": {
                            "headers": {
                                "Authorization": headers.get("Authorization", ""),
                                "Content-Type": "application/json",
                            },
                            "body": body_json,
                        }
                    },
                }
            except Exception as e:
                print(f"Error processing tools/call: {e}")
                return _deny_request(rpc_id, message=f"Error processing tool call: {str(e)}")

        # For other methods, pass through
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayRequest": {
                    "headers": {
                        "Authorization": headers.get("Authorization", ""),
                        "Content-Type": "application/json",
                    },
                    "body": body_json,
                }
            },
        }

    except Exception as e:
        print(f"Unexpected error in lambda_handler: {e}")
        # Return a safe error response
        return _deny_request(None, message=f"Internal error: {str(e)}")


def _deny_request(rpc_id, message: str):
    """Build a valid MCP/JSON-RPC error response"""
    print(f"Denying request: {message}")
    error_rpc = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": message,
                }
            ],
        },
    }
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                },
                "body": error_rpc,
            }
        },
    }
