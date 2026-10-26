"""MCP Order Management Server — exposes order CRUD tools via FastMCP."""

import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="order-management-tools",
    instructions="A collection of order management tools for creating, updating, and managing orders.",
    host="0.0.0.0",  # nosec B104
    stateless_http=True,
)


@mcp.tool()
def create_order(customer_name: str, product: str, quantity: int) -> str:
    """Create a new order for a customer."""
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    return (
        f"Order created successfully. Order ID: {order_id}, Customer: {customer_name}, "
        f"Product: {product}, Quantity: {quantity}, Status: PENDING, "
        f"Created: {datetime.now(tz=timezone.utc).isoformat()}"
    )


@mcp.tool()
def get_order(order_id: str) -> str:
    """Retrieve details of an existing order by its ID."""
    return (
        f"Order ID: {order_id}, Customer: Jane Smith, Product: Wireless Headphones, "
        f"Quantity: 2, Status: SHIPPED, Total: $149.98, "
        f"Created: 2025-01-15T10:30:00, Shipped: 2025-01-16T14:00:00"
    )


@mcp.tool()
def update_order(order_id: str, quantity: int | None = None, product: str | None = None) -> str:
    """Update an existing order's quantity or product."""
    updates = []
    if quantity is not None:
        updates.append(f"Quantity: {quantity}")
    if product is not None:
        updates.append(f"Product: {product}")
    return (
        f"Order {order_id} updated successfully. "
        f"Changes: {', '.join(updates) if updates else 'None'}, "
        f"Updated: {datetime.now(tz=timezone.utc).isoformat()}"
    )


@mcp.tool()
def cancel_order(order_id: str, reason: str) -> str:
    """Cancel an existing order with a reason."""
    return (
        f"Order {order_id} cancelled successfully. Reason: {reason}, "
        f"Status: CANCELLED, Cancelled: {datetime.now(tz=timezone.utc).isoformat()}"
    )


@mcp.tool()
def list_orders(status: str = "ALL") -> str:
    """List orders, optionally filtered by status."""
    return (
        f"Orders (filter: {status}):\n"
        f"  1. ORD-A1B2C3D4 | Jane Smith    | Wireless Headphones (2) | SHIPPED\n"
        f"  2. ORD-E5F6G7H8 | John Doe      | USB-C Cable (5)        | PENDING\n"
        f"  3. ORD-I9J0K1L2 | Alice Johnson | Laptop Stand (1)       | DELIVERED"
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
