# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Sample MCP server exposed through an Amazon Bedrock AgentCore Gateway.

The Gateway is the MCP server; this Lambda is its *target*. The Gateway terminates
the MCP protocol (initialize / tools/list / tools/call), so this function never sees
JSON-RPC. Per invocation it receives:

  event   -- a FLAT dict of the tool's inputSchema properties, e.g.
             {"registryId": "abc", "recordType": "MCP"}
  context -- the tool name in context.client_context.custom['bedrockAgentCoreToolName'],
             prefixed with the gateway TARGET name and a triple underscore:
             "<targetName>___<toolName>". Stripping that prefix is the single most
             common source of bugs in a Gateway Lambda, so it is done once, here.

The return value is any JSON-serialisable object; the Gateway wraps it into the MCP
tools/call result. Raising an exception surfaces as an MCP tool error.

Tools are intentionally trivial and dependency-free (standard library only) so the
sample deploys as a plain zip with no build step.
"""

import datetime
import json
import logging
from typing import ClassVar

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# The Gateway prefixes the visible tool name with "<targetName>___".
TOOL_NAME_DELIMITER = "___"


def _tool_name(context) -> str:
    """Resolve the bare tool name from the Lambda client context."""
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    raw = custom.get("bedrockAgentCoreToolName", "")
    if not raw:
        raise ValueError(
            "bedrockAgentCoreToolName missing from client context -- this function is "
            "meant to be invoked by an AgentCore Gateway MCP target, not directly."
        )
    if TOOL_NAME_DELIMITER in raw:
        return raw.split(TOOL_NAME_DELIMITER, 1)[1]
    return raw


def _echo(event: dict) -> dict:
    """Return the message back, with a server-side timestamp."""
    message = event.get("message")
    if not message:
        raise ValueError("`message` is required")
    return {
        "echoed": message,
        "receivedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _describe_registry_record_types(event: dict) -> dict:
    """
    Describe the Agent Registry record types. A read-only, deterministic tool that
    gives an agent something genuinely useful to call while proving the wiring.
    """
    catalog = {
        "MCP": {
            "descriptor": "mcpServer",
            "dataSchemaVersion": "2025-12-11",
            "payload": "MCP server.json (name / description / version)",
        },
        "AGENT": {
            "descriptor": "a2aAgentCard",
            "dataSchemaVersion": "0.3",
            "payload": "A2A agent card (protocolVersion / url / skills)",
        },
        "SKILL": {
            "descriptor": "agentSkillsDefinition",
            "dataSchemaVersion": "0.1.0",
            "payload": "{websiteUrl, repository:{url, source}}",
        },
        "CUSTOM": {
            "descriptor": "custom",
            "dataSchemaVersion": None,
            "payload": "any valid JSON",
        },
    }
    requested = event.get("recordType")
    if requested:
        key = str(requested).upper()
        if key not in catalog:
            raise ValueError(f"unknown recordType {requested!r}; expected one of {sorted(catalog)}")
        return {"recordType": key, **catalog[key]}
    return {"recordTypes": catalog}


TOOLS = {
    "echo": _echo,
    "describe_registry_record_types": _describe_registry_record_types,
}


def lambda_handler(event, context):
    tool = _tool_name(context)
    logger.info("tool=%s event_keys=%s", tool, sorted(event or {}))

    handler = TOOLS.get(tool)
    if handler is None:
        raise ValueError(f"unknown tool {tool!r}; this target exposes {sorted(TOOLS)}")

    result = handler(event or {})
    # Log the shape, never the full payload, so tool arguments stay out of CloudWatch.
    logger.info("tool=%s ok keys=%s", tool, sorted(result))
    return result


if __name__ == "__main__":  # local smoke test, no AWS needed

    class _Ctx:
        class client_context:
            custom: ClassVar[dict] = {"bedrockAgentCoreToolName": "SampleTarget___echo"}

    print(json.dumps(lambda_handler({"message": "hello"}, _Ctx()), indent=2))

    class _Ctx2:
        class client_context:
            custom: ClassVar[dict] = {"bedrockAgentCoreToolName": "SampleTarget___describe_registry_record_types"}

    print(json.dumps(lambda_handler({"recordType": "mcp"}, _Ctx2()), indent=2))
