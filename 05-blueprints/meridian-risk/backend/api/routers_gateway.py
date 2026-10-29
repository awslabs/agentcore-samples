# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""`/api/gateway/*` — inspect the MCP tool catalog and invoke a tool."""

import base64
import json
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException

import aws
from models import ToolInvokeRequest

router = APIRouter(prefix="/gateway")


@router.get("/tools")
def list_gateway_tools() -> dict[str, Any]:
    """List the Gateway's targets and the tool schemas they advertise."""
    gateway_id = aws.require(aws.GATEWAY_ID, "aws.GATEWAY_ID")
    client = aws.client("bedrock-agentcore-control")

    try:
        targets = client.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=100
        ).get("items", [])

        detailed = []
        for target in targets:
            detail = client.get_gateway_target(
                gatewayIdentifier=gateway_id, targetId=target["targetId"]
            )
            configuration = detail.get("targetConfiguration", {})
            tools = (
                configuration.get("mcp", {})
                .get("lambda", {})
                .get("toolSchema", {})
                .get("inlinePayload", [])
            )
            # A gateway can now host tool targets and model targets side by
            # side, so label which kind this is: an inference target legitimately
            # has zero tools and should not read as a broken MCP target.
            # Inference configs currently deserialize as SDK_UNKNOWN_MEMBER on
            # some botocore versions, hence checking both spellings.
            if "mcp" in configuration:
                kind = "mcp"
            elif "inference" in configuration or "SDK_UNKNOWN_MEMBER" in configuration:
                kind = "inference"
            elif "http" in configuration:
                kind = "http"
            else:
                kind = "unknown"

            detailed.append(
                {
                    "target_id": target["targetId"],
                    "name": target.get("name"),
                    "status": target.get("status"),
                    "kind": kind,
                    "tools": tools,
                }
            )
    except ClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "gateway_id": gateway_id,
        "gateway_url": aws.GATEWAY_URL,
        "targets": detailed,
        "tool_count": sum(len(t["tools"]) for t in detailed),
    }


@router.post("/invoke")
def invoke_gateway_tool(request: ToolInvokeRequest) -> dict[str, Any]:
    """Invoke one KYC tool and return its payload.

    Calls the backing Lambda directly with the Gateway's client-context shape.
    This keeps the tool inspector synchronous and dependency-free; the agents
    themselves always go through the Gateway over MCP.
    """
    function_name = aws.require(aws.KYC_TOOLS_LAMBDA, "aws.KYC_TOOLS_LAMBDA")


    client_context = base64.b64encode(
        json.dumps(
            {"custom": {"bedrockAgentCoreToolName": f"kyc-tools___{request.tool_name}"}}
        ).encode()
    ).decode()

    try:
        response = aws.client("lambda").invoke(
            FunctionName=function_name,
            Payload=json.dumps({"customer_id": request.customer_id}).encode(),
            ClientContext=client_context,
        )
    except ClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    raw = json.loads(response["Payload"].read())
    content = raw.get("content", [])
    text = content[0].get("text", "{}") if content else "{}"

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"raw": text}

    return {"tool": request.tool_name, "customer_id": request.customer_id, "result": parsed}
