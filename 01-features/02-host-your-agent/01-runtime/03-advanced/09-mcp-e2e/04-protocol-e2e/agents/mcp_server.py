"""Protocol basics on AgentCore Runtime: no handshake, runtime sessions, and errors that keep their HTTP status.

The tools are small probes. whoami reports which microVM answered and the session headers it received;
get_budget raises a tool error; simulate_server_fault is answered with HTTP 500 by a middleware, to show
how AgentCore Runtime reports a server fault.
"""

from __future__ import annotations

from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel
from runtime_utils import add_ping, instance_id, serve
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

mcp = MCPServer(name="finance-protocol-demo")
add_ping(mcp)

SESSION_HEADER = "x-amzn-bedrock-agentcore-runtime-session-id"
BUDGETS = {"food": 400.0, "transport": 150.0}


class WhoAmI(BaseModel):
    instance_id: str  # which microVM answered (created on its first request)
    protocol_version: str | None
    runtime_session_id: str | None  # as the server received it
    mcp_session_id: str | None


class Budget(BaseModel):
    category: str
    monthly_limit: float


@mcp.tool()
def whoami(ctx: Context) -> WhoAmI:
    """Report which microVM handled the request and the session headers it received."""
    headers = Headers(headers=dict(ctx.headers or {}))
    return WhoAmI(
        instance_id=instance_id(),
        protocol_version=ctx.protocol_version,
        runtime_session_id=headers.get(SESSION_HEADER),
        mcp_session_id=headers.get("mcp-session-id"),
    )


@mcp.tool()
def get_budget(category: Literal["food", "transport", "entertainment"]) -> Budget:
    """Return the monthly budget for a category. A category without a budget is a tool error."""
    if category not in BUDGETS:
        # A tool error is a result (isError: true), not a protocol error, so it arrives with HTTP 200.
        raise ToolError(f"No budget set for {category}")
    return Budget(category=category, monthly_limit=BUDGETS[category])


@mcp.tool()
def simulate_server_fault() -> str:
    """Demo only: ServerFault answers this call with HTTP 500 before it reaches the tool."""
    return "No fault: the request had no Mcp-Name header."


class ServerFault:
    """Answer tools/call simulate_server_fault with HTTP 500.

    It routes on the Mcp-Method and Mcp-Name headers without parsing the body, which is what the
    2026-07-28 standard headers make possible for proxies and gateways.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mcp":
            headers = Headers(scope=scope)
            if headers.get("mcp-method") == "tools/call" and headers.get("mcp-name") == "simulate_server_fault":
                await JSONResponse({"message": "simulated server fault"}, status_code=500)(scope, receive, send)
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    serve(mcp, wrap=ServerFault)
