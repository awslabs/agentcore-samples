"""MCP Server that exposes Microsoft Graph tools.

Auth model:
- MCP transport:   authorized via AgentCore customJWTAuthorizer on the MCP Server app's M2M tokens.
- Graph calls:     the agent sends the Graph-scoped OBO token in a custom request header
                   (X-Amzn-Bedrock-AgentCore-Runtime-Custom-Graph-Token). The MCP server reads
                   it from the request context and uses it as the Bearer credential to Microsoft Graph.

Why the Graph token travels via a header, not a tool argument:
- The LLM must never see or handle credentials (RFC 9700 §4.9; OWASP LLM06).
- Tool signatures therefore contain only business-layer parameters.
- The header is TLS-protected in transit and visible only to the agent and the MCP server, both
  within the same trust boundary as the user's inbound JWT.
"""

import httpx
from mcp.server.fastmcp import FastMCP
from typing import Dict, Any

mcp = FastMCP(host="0.0.0.0", stateless_http=True)  # nosec B104

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# AgentCore only forwards request headers that are explicitly allowlisted on the runtime and
# either named 'Authorization' or prefixed with 'X-Amzn-Bedrock-AgentCore-Runtime-Custom-'.
# The allowlist is set on the MCP runtime in Step 4. See:
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html
GRAPH_TOKEN_HEADER = "x-amzn-bedrock-agentcore-runtime-custom-graph-token"


def _get_graph_token() -> str:
    """Read the Graph OBO token the agent attached to this request."""
    ctx = mcp.get_context()
    headers = dict(ctx.request_context.request.headers)
    token = headers.get(GRAPH_TOKEN_HEADER, "").strip()
    if not token:
        raise RuntimeError(
            f"Missing Graph OBO token. Expected request header '{GRAPH_TOKEN_HEADER}' "
            "to carry the delegation token. Check that the header is allowlisted on the "
            "MCP runtime's requestHeaderConfiguration."
        )
    return token


@mcp.tool()
async def get_my_profile() -> Dict[str, Any]:
    """Return the signed-in user's Microsoft Graph profile."""
    access_token = _get_graph_token()
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{GRAPH_BASE}/me", headers={"Authorization": f"Bearer {access_token}"})
    if r.status_code != 200:
        return {"error": f"Graph returned {r.status_code}", "body": r.text}
    p = r.json()
    return {
        "displayName": p.get("displayName"),
        "email": p.get("mail") or p.get("userPrincipalName"),
        "jobTitle": p.get("jobTitle"),
        "id": p.get("id"),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
