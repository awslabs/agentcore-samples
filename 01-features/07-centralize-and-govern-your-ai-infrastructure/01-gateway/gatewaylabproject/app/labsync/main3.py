import json

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


@mcp.tool()
def archiveOrder(orderId: int) -> int:
    """Archive existing order"""
    return 202


@mcp.prompt()
def order_summary_prompt(orderId: int) -> str:
    """Prompt template that asks an LLM to summarize a single order."""
    return f"Summarize the activity on order {orderId}."


@mcp.resource("orders://catalog")
def order_catalog() -> str:
    """Static catalog of orders."""
    return json.dumps(
        {
            "orders": [
                {"id": 123, "customer": "alice", "total": 42.00},
                {"id": 456, "customer": "bob", "total": 99.50},
            ]
        }
    )


@mcp.resource("orders://{orderId}/details")
def order_details(orderId: str) -> str:
    """Templated resource: details for a specific order ID."""
    return json.dumps({"orderId": orderId, "status": "shipped", "carrier": "UPS"})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
