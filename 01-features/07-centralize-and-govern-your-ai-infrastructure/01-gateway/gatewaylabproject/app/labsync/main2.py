from mcp.server.fastmcp import FastMCP

mcp = FastMCP(host="0.0.0.0", stateless_http=True)  # nosec B104 - AgentCore Runtime container requires bind to all interfaces


@mcp.tool()
def getOrder() -> int:
    """Get an order"""
    return 123


@mcp.tool()
def updateOrder(orderId: int) -> int:
    """Update existing order"""
    return 456


@mcp.tool()
def cancelOrder(orderId: int) -> int:
    """Cancel existing order"""
    return 789


@mcp.tool()
def deleteOrder(orderId: int) -> int:
    """Delete existing order"""
    return 101


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
