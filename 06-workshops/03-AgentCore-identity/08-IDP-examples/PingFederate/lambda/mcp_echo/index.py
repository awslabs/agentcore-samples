# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Minimal MCP server on Lambda — gateway target for the inbound auth demo.

Exposes two tools (get_time, echo) via the MCP Streamable HTTP protocol.
Deployed by CDK as part of the PingFederate sample.
"""

import json
from datetime import datetime, timezone

TOOLS = [
    {
        "name": "get_time",
        "description": "Get the current UTC time",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "echo",
        "description": "Echo a message back",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "Message to echo"}},
            "required": ["message"],
        },
    },
]


def handle_request(body):
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "PingFederateDemoMCP", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        if name == "get_time":
            text = datetime.now(timezone.utc).isoformat()
        elif name == "echo":
            text = f"Echo: {args.get('message', '')}"
        else:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                "id": req_id,
            }
        result = {"content": [{"type": "text", "text": text}]}
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return None
    else:
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": f"Method not found: {method}"},
            "id": req_id,
        }

    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        if isinstance(body, list):
            responses = [r for r in (handle_request(r) for r in body) if r is not None]
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(responses),
            }
        response = handle_request(body)
        if response is None:
            return {"statusCode": 202, "body": ""}
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(response),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
