# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Lambda target for the AgentCore Gateway in the Keycloak sample.

Implements two tools, get_time and echo. The gateway publishes their schemas (see TOOLS in
setup_agentcore.py) and invokes this function for tools/call:

- event: the tool arguments, for example {"message": "hello"}
- context.client_context.custom["bedrockAgentCoreToolName"]: "<target name>___<tool name>"

For details, see
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html
"""

from datetime import UTC, datetime

TOOL_NAME_DELIMITER = "___"


def tool_name_from_context(context) -> str:
    """Return the tool name without the gateway target prefix."""
    full_name = context.client_context.custom["bedrockAgentCoreToolName"]
    return full_name.split(TOOL_NAME_DELIMITER, 1)[-1]


def handler(event, context):
    """Run the tool that the gateway requested."""
    tool_name = tool_name_from_context(context)
    if tool_name == "get_time":
        return {"time": datetime.now(UTC).isoformat()}
    if tool_name == "echo":
        return {"echo": event.get("message", "")}
    return {"error": f"Unknown tool: {tool_name}"}
