"""An existing MCP server, written for MCP Python SDK v1 before MCP 2026-07-28. Unchanged.

It has the shape of the basic MCP server in 02-hosting-tools/01-mcp-server-basics: FastMCP in
stateless mode with JSON responses. It speaks MCP 2025-11-25 and earlier only, so it answers a
2026-07-28 request with HTTP 400. An MCP Python SDK v2 client in its default mode="auto" then falls
back to the initialize handshake and keeps working; a client pinned to 2026-07-28 fails.

    python mcp_server_v1.py      # listens on 127.0.0.1:8000
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP

# Loopback by default. deploy.py sets MCP_HOST=0.0.0.0, which AgentCore Runtime requires; the runtime
# publishes no port and authenticates every request before it reaches the server.
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8000"))

# stateless_http=True is what AgentCore Runtime recommends for MCP 2025-11-25 and earlier, and
# json_response=True makes every reply a plain JSON body, as in the older sample.
mcp = FastMCP("finance-basics", host=HOST, port=PORT, stateless_http=True, json_response=True)

EXPENSES = [{"amount": 230.0, "category": "food"}, {"amount": 45.0, "category": "transport"}]
_instance: dict[str, str] = {}


def instance_id() -> str:
    """A per-microVM ID, created on the first request (an ID created at import would be shared)."""
    if not _instance:
        _instance["id"] = uuid.uuid4().hex[:12]
    return _instance["id"]


@mcp.tool()
def get_total() -> dict[str, Any]:
    """Total the expenses."""
    return {"expenses": len(EXPENSES), "total": sum(e["amount"] for e in EXPENSES)}


@mcp.tool()
def greet(name: str, language: Literal["english", "portuguese", "french"] = "english") -> str:
    """Greet someone in English, Portuguese, or French."""
    return {
        "english": f"Hello, {name}!",
        "portuguese": f"Olá, {name}!",
        "french": f"Bonjour, {name} !",
    }[language]


@mcp.tool()
def whoami(ctx: Context) -> dict[str, Any]:
    """Report which microVM handled the request and the session headers it received."""
    request = ctx.request_context.request
    headers = request.headers if request is not None else {}
    return {
        "instance_id": instance_id(),
        "runtime_session_id": headers.get("x-amzn-bedrock-agentcore-runtime-session-id"),
        "mcp_session_id": headers.get("mcp-session-id"),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
