from mcp.server.fastmcp import FastMCP

mcp = FastMCP(host="0.0.0.0", stateless_http=True)  # nosec B104


@mcp.tool()
def echo_headers(message: str) -> str:
    """Echo back the message along with any propagated headers received by this MCP server."""
    return f"MCP server received: {message}"


@mcp.tool()
def get_order(orderId: str) -> str:
    """Get order details."""
    return f"Order {orderId}: shipped"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
