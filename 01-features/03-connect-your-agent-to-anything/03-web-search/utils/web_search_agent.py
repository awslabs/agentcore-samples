"""
Shared Strands agent using AgentCore web search tool via Gateway.

Used as the common demo agent across web search sub-demos:
  - 03-strands-agent/web_search_strands.py

The agent connects to an AgentCore gateway that exposes the Web Search Tool
as an MCP-compliant connector target. Tools are discovered dynamically via
the MCP tools/list endpoint.

Environment variables:
    BEDROCK_MODEL_ID  — (optional) Bedrock inference profile ID or ARN;
                        must be a cross-region inference profile (e.g. us.*, eu.*, ap.*)
                        or an inference profile ARN — on-demand model IDs are not supported.
                        Defaults to us.anthropic.claude-sonnet-4-5-20250514-v1:0
    AWS_DEFAULT_REGION — AWS region (default: us-east-1)
"""

import os

from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient

from utils.gateway_auth import create_streamable_http_transport

# ── Configuration ─────────────────────────────────────────────────────────────

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")

SYSTEM_PROMPT = """You are a helpful research assistant with access to real-time web search.

PRINCIPLES:
- Use the WebSearch tool to find current information when answering questions
- Always cite your sources with URLs when providing information from search results
- If search results are insufficient, say so rather than guessing
- Keep queries concise (under 200 characters) for best results
- Synthesize information from multiple results when possible

RESPONSE FORMAT:
- Provide clear, well-structured answers
- Include source URLs for verification
- Note the publication date of sources when available
"""


# ── Factory ────────────────────────────────────────────────────────────────────


def create_mcp_client(gateway_url: str = "", **token_kwargs) -> MCPClient:
    """Create an MCPClient connected to the AgentCore gateway.

    Args:
        gateway_url: The Gateway MCP endpoint URL.
        **token_kwargs: Passed to gateway_auth for credential overrides.

    Returns:
        MCPClient instance (must be used as a context manager).
    """
    transport_factory = create_streamable_http_transport(gateway_url=gateway_url, **token_kwargs)
    return MCPClient(transport_factory)


def create_agent(mcp_client: MCPClient) -> Agent:
    """Create a Strands agent with Web Search tools from the Gateway.

    The mcp_client must already be entered as a context manager (i.e.,
    call this inside a `with mcp_client:` block).

    Args:
        mcp_client: An active MCPClient connected to the Gateway.

    Returns:
        Strands Agent configured with discovered tools.
    """
    tools = mcp_client.list_tools_sync()
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    return Agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )
