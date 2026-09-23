import os
from typing import Optional
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient


def get_streamable_http_mcp_client(user_token: Optional[str] = None) -> MCPClient:
    """
    Returns an MCP Client for AgentCore Gateway compatible with Strands.

    Args:
        user_token: User JWT token with gateway:invoke scope.
    """
    gateway_url = os.getenv("GATEWAY_URL")

    # Handle local development mode
    if os.getenv("LOCAL_DEV") == "1":
        from contextlib import nullcontext
        from types import SimpleNamespace

        return nullcontext(SimpleNamespace(list_tools_sync=lambda: []))

    if not gateway_url:
        raise RuntimeError("Missing required environment variable: GATEWAY_URL")

    if not user_token:
        raise RuntimeError("User token is required for Gateway access")

    return MCPClient(lambda: streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {user_token}"}))
