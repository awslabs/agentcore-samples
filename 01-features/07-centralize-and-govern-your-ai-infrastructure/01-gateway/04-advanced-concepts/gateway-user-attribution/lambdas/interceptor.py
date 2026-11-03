"""AgentCore Gateway RESPONSE interceptor - user attribution + result capture.

Runs inside the gateway on every MCP response, AFTER the target has produced a
result but BEFORE the gateway returns it to the caller. For an MCP target the
RESPONSE interceptor's input carries BOTH the original request (including the
validated ``Authorization`` JWT, because the gateway is configured with
``passRequestHeaders=true``) AND the gateway's response body. That lets a single
interceptor emit one record that ties together:

    which user  ->  called which tool  ->  with which arguments  ->  and got which result

...with zero instrumentation in the MCP servers. Everything lands in this
Lambda's own CloudWatch log group, so no cross-log-group join is needed to see
identity and result together.

Payload shape reference:
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-types.html
"""

import base64
import json
import time

TOOL_NAME_DELIMITER = "___"


def _decode_jwt_claims(auth_header):
    """Decode (not verify) the JWT payload from an Authorization header."""
    if not auth_header:
        return {}
    token = auth_header.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # pad base64url
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        return json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return {}


def _passthrough():
    """Return the response unchanged (empty transform = pass through)."""
    return {"interceptorOutputVersion": "1.0", "mcp": {}}


def lambda_handler(event, context):
    mcp = (event or {}).get("mcp", {})
    gateway_request = mcp.get("gatewayRequest", {}) or {}
    gateway_response = mcp.get("gatewayResponse", {}) or {}

    headers = gateway_request.get("headers", {}) or {}
    req_body = gateway_request.get("body", {}) or {}
    resp_body = gateway_response.get("body", {}) or {}

    # Headers are case-preserving; look them up case-insensitively.
    auth_header = next(
        (v for k, v in headers.items() if k.lower() == "authorization"), None
    )
    claims = _decode_jwt_claims(auth_header)

    method = req_body.get("method")
    params = req_body.get("params", {}) or {}

    # Only attribute actual tool calls; pass everything else through quietly.
    if method == "tools/call":
        raw_tool = params.get("name", "")
        tool = (
            raw_tool.split(TOOL_NAME_DELIMITER, 1)[1]
            if TOOL_NAME_DELIMITER in raw_tool
            else raw_tool
        )
        # The MCP result content is a list of content blocks; capture the text
        # of the first block as a compact "response" field, and keep the full
        # result for anyone who wants it.
        result = resp_body.get("result", {}) if isinstance(resp_body, dict) else {}
        response_text = None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                response_text = first.get("text")

        record = {
            "signal": "mcp.tool_call",
            "timestamp": int(time.time() * 1000),
            "user": {
                "sub": claims.get("sub"),
                "username": claims.get("username") or claims.get("cognito:username"),
                "email": claims.get("email"),
                "client_id": claims.get("client_id"),
            },
            "tool": tool,
            "target": (
                raw_tool.split(TOOL_NAME_DELIMITER, 1)[0]
                if TOOL_NAME_DELIMITER in raw_tool
                else None
            ),
            "arguments": params.get("arguments", {}),
            "status_code": gateway_response.get("statusCode"),
            "response": response_text,
            "mcp_session_id": next(
                (v for k, v in headers.items() if k.lower() == "mcp-session-id"), None
            ),
        }
        # Single-line JSON so it is easy to query in CloudWatch Logs Insights.
        print(json.dumps(record))

    # Return the response unchanged.
    return _passthrough()
