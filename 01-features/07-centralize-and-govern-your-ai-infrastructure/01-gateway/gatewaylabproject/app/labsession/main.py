"""MCP server for the session-management notebook (05-session-management.ipynb).

Demonstrates:
  - Session continuity across tool calls   (session_counter)
  - Session isolation (counter resets on a fresh `Mcp-Session-Id`)
  - Persisted client-sid ↔ target-sid mapping (visible via the per-session counter)

This server is deployed to AgentCore Runtime and surfaced through a gateway
configured with `sessionConfiguration` enabled and NO
`streamingConfiguration.enableResponseStreaming` (streaming disabled). No
Lambda interceptor.
"""

from collections import defaultdict
from contextlib import asynccontextmanager

from fastmcp import Context, FastMCP


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Per-process state — counters keyed by `Mcp-Session-Id`."""
    yield {"session_counters": defaultdict(int)}


mcp = FastMCP(name="labsession", lifespan=lifespan)


def _session_id(ctx: Context) -> str:
    """Best-effort session id extraction across fastmcp versions."""
    return (
        getattr(ctx, "session_id", None)
        or getattr(getattr(ctx, "session", None), "session_id", None)
        or "unknown"
    )


@mcp.tool()
async def session_counter(ctx: Context) -> dict:
    """Per-session counter — calls within the same `Mcp-Session-Id` see incrementing values."""
    counters = ctx.request_context.lifespan_context["session_counters"]
    sid = _session_id(ctx)
    counters[sid] += 1
    return {"session_id": sid, "count": counters[sid]}


@mcp.tool()
def getOrder() -> int:
    """Trivial sync sanity tool."""
    return 123


@mcp.tool()
def updateOrder(orderId: int) -> int:
    """Trivial sync sanity tool — returns a fixed ack."""
    return 456


if __name__ == "__main__":
    # stateless_http=False keeps fastmcp's internal session id available; the
    # gateway's `sessionConfiguration` provides the *client*-facing session id.
    mcp.run(transport="streamable-http", host="0.0.0.0", stateless_http=False)  # nosec B104 - AgentCore Runtime container requires bind to all interfaces
