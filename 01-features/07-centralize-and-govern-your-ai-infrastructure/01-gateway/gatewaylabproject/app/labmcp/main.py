from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
import json

mcp = FastMCP(host="0.0.0.0", stateless_http=True)  # nosec B104 - AgentCore Runtime container requires bind to all interfaces


# --- Tools ---------------------------------------------------------------


@mcp.tool(
    title="Get Order",
    annotations=ToolAnnotations(
        title="Get Order",
        readOnlyHint=True,
        idempotentHint=True,
    ),
)
def getOrder() -> int:
    """Get an order."""
    return 123


@mcp.tool(
    title="Update Order",
    annotations=ToolAnnotations(
        title="Update Order",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
def updateOrder(orderId: int) -> int:
    """Update an existing order."""
    return 456


# --- Prompts -------------------------------------------------------------


@mcp.prompt()
def order_summary_prompt(orderId: int) -> str:
    """Prompt template that asks an LLM to summarize a single order."""
    return f"Summarize the activity on order {orderId}."


@mcp.prompt()
def cancellation_email_prompt(orderId: int, reason: str) -> str:
    """Prompt template for drafting a customer cancellation email."""
    return f"Draft a customer email cancelling order {orderId}. Reason: {reason}."


# --- Resources (static) --------------------------------------------------


@mcp.resource("orders://catalog")
def order_catalog() -> str:
    """Static catalog of orders the server knows about."""
    return json.dumps(
        {
            "orders": [
                {"id": 123, "customer": "alice", "total": 42.00},
                {"id": 456, "customer": "bob", "total": 99.50},
            ]
        }
    )


@mcp.resource("exa://tools/list")
def shadowed_exa_tools_list() -> str:
    """Intentionally collides with the Exa MCP server's `exa://tools/list` resource."""
    return (
        "Shadowed by mcp_server.py — runtime resourcePriority (10) wins over Exa (100)."
    )


# --- Resources (templated) ----------------------------------------------


@mcp.resource("orders://{orderId}/details")
def order_details(orderId: str) -> str:
    """Templated resource: details for a specific order ID."""
    return json.dumps({"orderId": orderId, "status": "shipped", "carrier": "UPS"})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
