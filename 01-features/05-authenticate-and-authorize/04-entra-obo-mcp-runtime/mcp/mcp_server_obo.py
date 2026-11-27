"""Serve one profile tool. The transport bearer is validated by Runtime, not forwarded to Graph."""

import httpx
from mcp.server.mcpserver import Context, MCPServer

mcp = MCPServer("entra-profile")
HEADER = "x-amzn-bedrock-agentcore-runtime-custom-graph-token"


@mcp.tool()
async def get_my_profile(ctx: Context) -> dict:
    """Read the signed-in user's display name and job title. No credential arguments."""
    request = ctx.request_context.request
    grant = request.headers.get(HEADER) if request else None
    if not grant:
        raise ValueError("Delegated user context is required")
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
        r = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            params={"$select": "displayName,jobTitle"},
            headers={"Authorization": "Bearer " + grant},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Graph profile unavailable (HTTP {r.status_code})")
    data = r.json()
    return {"displayName": data.get("displayName"), "jobTitle": data.get("jobTitle")}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000, stateless_http=True)  # nosec B104 -- container listener
