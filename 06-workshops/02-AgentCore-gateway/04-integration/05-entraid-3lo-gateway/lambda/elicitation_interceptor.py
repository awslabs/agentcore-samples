# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Gateway RESPONSE interceptor that catches elicitation responses (-32042)
and rewrites them into friendly messages pointing users to the auth
onboarding app.

When the Gateway detects a missing downstream token, it returns an
elicitation error asking the client to open an authorization URL. This
doesn't work in VS Code because our 3LO flow is designed for the web
app. Instead, this interceptor converts the elicitation into a normal
tools/call result with a human-readable message.
"""

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AUTH_ONBOARDING_URL = os.environ.get("AUTH_ONBOARDING_URL", "")

# MCP JSON-RPC error code for elicitation (authorization required)
ELICITATION_ERROR_CODE = -32042


def lambda_handler(event, context):
    logger.info("Interceptor event: %s", json.dumps(event, default=str)[:2000])

    mcp_data = event.get("mcp", {})
    gateway_response = mcp_data.get("gatewayResponse")

    if not gateway_response:
        # Not a response interceptor call — pass through
        logger.warning("No gatewayResponse in event — passing through")
        return passthrough_response(mcp_data)

    body = gateway_response.get("body") or {}

    # body might be a string — parse it
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return passthrough_response(mcp_data)

    if not isinstance(body, dict):
        return passthrough_response(mcp_data)

    # Check if this is an elicitation error
    error = body.get("error")
    if not error or error.get("code") != ELICITATION_ERROR_CODE:
        # Not an elicitation — pass through unchanged
        return passthrough_response(mcp_data)

    # Check if the caller explicitly wants the raw elicitation (e.g. auth onboarding SPA).
    # The SPA sends _meta.rawElicitation: true in the JSON-RPC request to signal
    # "I know what I'm doing, give me the elicitation so I can handle it."
    request_body = mcp_data.get("gatewayRequest", {}).get("body", {})
    if isinstance(request_body, str):
        try:
            request_body = json.loads(request_body)
        except (json.JSONDecodeError, TypeError):
            request_body = {}

    meta = request_body.get("_meta", {}) if isinstance(request_body, dict) else {}
    if meta.get("rawElicitation"):
        logger.info("rawElicitation flag detected — passing elicitation through raw")
        return passthrough_response(mcp_data)

    logger.info("Detected elicitation response — rewriting to friendly message")

    jsonrpc_id = request_body.get("id", body.get("id", 1))

    message = (
        "\u26a0\ufe0f Authorization Required\n\n"
        "You haven't authorized access to the downstream API yet. "
        "Please visit our auth onboarding app to complete authorization:\n\n"
        f"{AUTH_ONBOARDING_URL}\n\n"
        "After completing authorization there, retry this tool call."
    )

    rewritten_body = {
        "jsonrpc": "2.0",
        "id": jsonrpc_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": message,
                }
            ]
        },
    }

    response = {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 200,
                "body": rewritten_body,
            }
        },
    }
    logger.info("Returning rewritten response: %s", json.dumps(response, default=str)[:1000])
    return response


def passthrough_response(mcp_data):
    """Pass the original response through unchanged."""
    gateway_response = mcp_data.get("gatewayResponse", {})
    body = gateway_response.get("body")
    status_code = gateway_response.get("statusCode", 200)
    # Gateway may send body as a JSON string but expects a dict back
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            body = {}
    # Gateway rejects null body — use empty dict for no-body responses (e.g. 202 notifications)
    if body is None:
        body = {}
    response = {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "body": body,
                "statusCode": status_code,
            }
        },
    }
    logger.info("Passthrough response: %s", json.dumps(response, default=str)[:1000])
    return response
