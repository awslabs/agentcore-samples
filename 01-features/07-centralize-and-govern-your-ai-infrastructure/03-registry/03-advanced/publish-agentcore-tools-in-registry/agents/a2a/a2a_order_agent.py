"""A2A Order Management Agent — deployed to AgentCore Runtime."""

import os
import uuid as _uuid
from datetime import datetime, timezone

import uvicorn
from a2a.types import AgentSkill
from fastapi import FastAPI
from strands import Agent, tool
from strands.multiagent.a2a import A2AServer

runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")
host, port = "0.0.0.0", 9000  # nosec B104


@tool
def create_order(customer_name: str, product: str, quantity: int) -> str:
    """Create a new order for a customer."""
    order_id = f"ORD-{_uuid.uuid4().hex[:8].upper()}"
    return (
        f"Order created successfully. Order ID: {order_id}, Customer: {customer_name}, "
        f"Product: {product}, Quantity: {quantity}, Status: PENDING, "
        f"Created: {datetime.now(tz=timezone.utc).isoformat()}"
    )


@tool
def get_order(order_id: str) -> str:
    """Retrieve details of an existing order by its ID."""
    return (
        f"Order ID: {order_id}, Customer: Jane Smith, Product: Wireless Headphones, "
        f"Quantity: 2, Status: SHIPPED, Total: $149.98"
    )


@tool
def cancel_order(order_id: str, reason: str) -> str:
    """Cancel an existing order with a reason."""
    return (
        f"Order {order_id} cancelled successfully. Reason: {reason}, "
        f"Status: CANCELLED, Cancelled: {datetime.now(tz=timezone.utc).isoformat()}"
    )


@tool
def list_orders(status: str = "ALL") -> str:
    """List orders, optionally filtered by status."""
    return f"Orders (filter: {status}):\n  1. ORD-A1B2C3D4 | Jane Smith | Wireless Headphones (2) | SHIPPED"


agent = Agent(
    system_prompt=(
        "You are an order management assistant. Use the available tools to "
        "create, retrieve, cancel, and list orders. Be concise and confirm actions clearly."
    ),
    tools=[create_order, get_order, cancel_order, list_orders],
    name="order-management-agent",
    description="An order management agent that handles order creation, cancellations, and lookups",
)

a2a_server = A2AServer(
    agent=agent,
    http_url=runtime_url,
    serve_at_root=True,
    skills=[
        AgentSkill(
            id="order-management",
            name="Order Management",
            description="Create, retrieve, update, and cancel customer orders",
            examples=[
                "Create an order for 2 headphones for Jane Smith",
                "Cancel order ORD-A1B2C3D4",
            ],
            tags=[],
        ),
        AgentSkill(
            id="order-tracking",
            name="Order Tracking",
            description="Look up order status and list orders by status",
            examples=[
                "What is the status of order ORD-A1B2C3D4?",
                "Show me all pending orders",
            ],
            tags=[],
        ),
    ],
)

app = FastAPI()


@app.get("/ping")
def ping():
    return {"status": "healthy"}


app.mount("/", a2a_server.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
