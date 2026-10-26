"""
An MCP server on AgentCore Runtime, running on your own EC2 instances.

This is NOT a BedrockAgentCoreApp. An HTTP agent uses the SDK's app object and
answers POST /invocations. An MCP server speaks JSON-RPC over the MCP
streamable-HTTP transport instead, so it is a FastMCP server and the SDK's
`BedrockAgentCoreApp` is not involved at all.

THE THREE THINGS THE RUNTIME REQUIRES
-------------------------------------
1. Port 8000. The AgentCore Runtime service contract fixes the MCP container
   port at 8000 (HTTP is 8080, A2A is 9000). Not negotiable, not configurable.
2. Host 0.0.0.0. Binding 127.0.0.1 makes the server unreachable from outside
   the instance, and the health check fails.
3. `stateless_http=True`. The runtime does not guarantee that two requests in a
   session reach the same server process, so the MCP server must not keep
   transport state between requests. FastMCP's default is stateful, which fails
   under a load balancer with "Missing session ID" errors.
4. Path `/mcp`. FastMCP's streamable-http transport mounts there by default,
   which is what the runtime expects.

The tools below are deliberately dull. The sample is about the protocol and the
compute, not about what the tools do.
"""

from __future__ import annotations

import os
import platform
import socket
import uuid

from mcp.server.fastmcp import FastMCP

# Generated once per process. Two calls that report the same PROCESS_ID were
# served by the same Python process on the same EC2 instance — the same trick
# Sample 2 uses to make session→instance affinity visible.
PROCESS_ID = uuid.uuid4().hex[:8]

# stateless_http=True is the load-balancer-safe mode. host/port are the service
# contract, not preferences.
mcp = FastMCP(host="0.0.0.0", port=8000, stateless_http=True)  # nosec B104


@mcp.tool()
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b


@mcp.tool()
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}! Served by MCP process {PROCESS_ID}."


@mcp.tool()
def whoami() -> dict:
    """
    Report the machine this MCP server is running on.

    This is the tool that proves the sample's point. A serverless MCP server
    cannot tell you its instance type; this one runs on an EC2 instance you
    chose, and the values below come from that instance.
    """
    try:
        memory_mb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError):
        memory_mb = None
    return {
        "process_id": PROCESS_ID,
        "hostname": socket.gethostname(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_mb": memory_mb,
        "protocol": "MCP",
        # Set by deploy.py so the running server can confirm which fleet it landed on.
        "instance_type": os.environ.get("FLEET_INSTANCE_TYPE", "unknown"),
    }


if __name__ == "__main__":
    # "streamable-http" is the transport AgentCore Runtime speaks. Not "stdio"
    # (no process to pipe to) and not the deprecated "sse".
    mcp.run(transport="streamable-http")
