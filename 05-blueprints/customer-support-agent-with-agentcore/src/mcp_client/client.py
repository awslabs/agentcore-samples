import os
import re

from strands.tools.mcp import MCPClient


def get_streamable_http_mcp_client(token: str, region: str) -> MCPClient:
    url = os.environ["GATEWAY_URL"]
    if not re.fullmatch(
        rf"https://[A-Za-z0-9-]+\.gateway\.bedrock-agentcore\.{re.escape(region)}\.amazonaws\.com/mcp", url
    ):
        raise ValueError("Expected a regional AgentCore Gateway HTTPS endpoint")
    if not token:
        raise ValueError("Authenticated caller required")
    return MCPClient(url=url, headers={"Authorization": "Bearer " + token})
