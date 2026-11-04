"""
Shared Strands agent integration for the AgentCore Web Answer Tool.

The helpers create an authenticated MCP client and a Strands agent whose tools
are discovered dynamically from AgentCore Gateway. The Gateway exposes the
managed Web Answer connector as the WebAnswer MCP tool.

Environment variables:
    BEDROCK_MODEL_ID       — Optional Bedrock inference profile ID or ARN;
                             defaults to global.anthropic.claude-sonnet-4-6
    AWS_DEFAULT_REGION     — AWS region, defaulting to us-east-1
    AGENTCORE_GATEWAY_URL  — Gateway MCP endpoint used by the auth utility
    COGNITO_*              — Cognito client credentials used by the auth utility
"""

import os
from datetime import date

from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient

from utils.gateway_auth import create_streamable_http_transport

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")

SYSTEM_PROMPT = f"""You are a helpful assistant. Today's date is {date.today().isoformat()}.

Use the WebAnswer tool for direct factual questions that require current web
information. Treat the tool's returned answer and citations as the grounded
source of truth. Preserve its source URLs when responding. If the tool cannot
produce a well-supported answer, say so rather than guessing.
"""


def create_mcp_client(gateway_url: str = "", **token_kwargs) -> MCPClient:
    """Create an MCP client authenticated to AgentCore Gateway.

    Args:
        gateway_url: Gateway MCP endpoint. An empty value uses the URL from the
            shared authentication configuration.
        **token_kwargs: Credential overrides forwarded to the OAuth token helper.

    Returns:
        An ``MCPClient`` that must be entered as a context manager before use.
    """
    transport_factory = create_streamable_http_transport(
        gateway_url=gateway_url,
        **token_kwargs,
    )
    return MCPClient(transport_factory)


def create_agent(mcp_client: MCPClient) -> Agent:
    """Create a Strands agent with tools discovered from AgentCore Gateway.

    The MCP client must already be active so that ``tools/list`` can discover
    WebAnswer and any other tools exposed by the Gateway.

    Args:
        mcp_client: Active MCP client entered through a ``with`` block.

    Returns:
        A Bedrock-backed Strands agent configured with the discovered tools and
        Web Answer system prompt.
    """
    tools = mcp_client.list_tools_sync()
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    return Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)
