"""Shared Strands agent for the AgentCore Web Answer Tool."""

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
    """Create an MCP client authenticated to the AgentCore Gateway."""
    transport_factory = create_streamable_http_transport(
        gateway_url=gateway_url,
        **token_kwargs,
    )
    return MCPClient(transport_factory)


def create_agent(mcp_client: MCPClient) -> Agent:
    """Create a Strands agent with tools discovered from the Gateway."""
    tools = mcp_client.list_tools_sync()
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    return Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)
