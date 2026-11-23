import json
import uuid
from typing import Any

# Mock Data Stores
CUSTOMERS = {
    "CUST-001": {"customer_id": "CUST-001", "name": "John Doe"},
    "CUST-002": {"customer_id": "CUST-002", "name": "Jane Smith"},
}

ORDERS = {
    # --- CUST-001 (John Doe) ---
    "ORD-12345": {
        "order_id": "ORD-12345",
        "customer_id": "CUST-001",
        "status": "delivered",
        "items": [{"name": "Wireless Headphones", "quantity": 1, "price": 79.99}],
        "total": 79.99,
        "order_date": "2025-01-15",
        "delivery_date": "2025-01-20",
    },
    "ORD-12300": {
        "order_id": "ORD-12300",
        "customer_id": "CUST-001",
        "status": "delivered",
        "items": [{"name": "Running Shoes", "quantity": 1, "price": 249.00}],
        "total": 249.00,
        "order_date": "2025-01-02",
        "delivery_date": "2025-01-08",
    },
    "ORD-12400": {
        "order_id": "ORD-12400",
        "customer_id": "CUST-001",
        "status": "delivered",
        "items": [{"name": "USB-C Charging Cable", "quantity": 2, "price": 12.99}],
        "total": 25.98,
        "order_date": "2025-01-20",
        "delivery_date": "2025-01-23",
    },
    "ORD-12410": {
        "order_id": "ORD-12410",
        "customer_id": "CUST-001",
        "status": "delivered",
        "items": [{"name": "Mechanical Keyboard", "quantity": 1, "price": 149.99}],
        "total": 149.99,
        "order_date": "2025-01-25",
        "delivery_date": "2025-01-29",
    },
    "ORD-12420": {
        "order_id": "ORD-12420",
        "customer_id": "CUST-001",
        "status": "delivered",
        "items": [{"name": "Phone Case", "quantity": 1, "price": 29.99}],
        "total": 29.99,
        "order_date": "2025-02-01",
        "delivery_date": "2025-02-04",
    },
    "ORD-12430": {
        "order_id": "ORD-12430",
        "customer_id": "CUST-001",
        "status": "delivered",
        "items": [{"name": "4K Monitor", "quantity": 1, "price": 399.00}],
        "total": 399.00,
        "order_date": "2025-02-05",
        "delivery_date": "2025-02-10",
    },
    # --- CUST-002 (Jane Smith) ---
    "ORD-99000": {
        "order_id": "ORD-99000",
        "customer_id": "CUST-002",
        "status": "delivered",
        "items": [{"name": "Premium Laptop", "quantity": 1, "price": 1299.00}],
        "total": 1299.00,
        "order_date": "2025-01-10",
        "delivery_date": "2025-01-15",
    },
    "ORD-99010": {
        "order_id": "ORD-99010",
        "customer_id": "CUST-002",
        "status": "delivered",
        "items": [{"name": "Yoga Mat", "quantity": 1, "price": 45.00}],
        "total": 45.00,
        "order_date": "2025-01-18",
        "delivery_date": "2025-01-21",
    },
    "ORD-99020": {
        "order_id": "ORD-99020",
        "customer_id": "CUST-002",
        "status": "delivered",
        "items": [{"name": "Bluetooth Speaker", "quantity": 1, "price": 89.99}],
        "total": 89.99,
        "order_date": "2025-01-22",
        "delivery_date": "2025-01-26",
    },
    "ORD-99030": {
        "order_id": "ORD-99030",
        "customer_id": "CUST-002",
        "status": "delivered",
        "items": [{"name": "Standing Desk", "quantity": 1, "price": 549.00}],
        "total": 549.00,
        "order_date": "2025-01-28",
        "delivery_date": "2025-02-03",
    },
    "ORD-99040": {
        "order_id": "ORD-99040",
        "customer_id": "CUST-002",
        "status": "delivered",
        "items": [{"name": "Notebook Set", "quantity": 3, "price": 8.99}],
        "total": 26.97,
        "order_date": "2025-02-02",
        "delivery_date": "2025-02-05",
    },
    "ORD-99050": {
        "order_id": "ORD-99050",
        "customer_id": "CUST-002",
        "status": "delivered",
        "items": [{"name": "Wireless Mouse", "quantity": 1, "price": 59.99}],
        "total": 59.99,
        "order_date": "2025-02-06",
        "delivery_date": "2025-02-09",
    },
}

REFUNDS = {}


def lambda_handler(event, context):
    """
    Lambda handler for Order APIs via Bedrock AgentCore Gateway.
    """
    try:
        extended_name = context.client_context.custom.get("bedrockAgentCoreToolName")
        tool_name = None

        if extended_name and "___" in extended_name:
            tool_name = extended_name.split("___", 1)[1]

        if not tool_name:
            return _response(400, {"error": "Missing tool name"})

        if tool_name == "get_order":
            return get_order(event)
        elif tool_name == "list_orders":
            return list_orders(event)
        elif tool_name == "process_refund":
            return process_refund(event)
        else:
            return _response(400, {"error": f"Unknown tool '{tool_name}'"})

    except (KeyError, TypeError, ValueError):
        return _response(400, {"error": "Invalid order request"})


def _response(status_code: int, body: dict[str, Any]):
    """Consistent JSON response wrapper."""
    return {"statusCode": status_code, "body": json.dumps(body)}


def get_order(event: dict[str, Any]):
    """Look up order details by order ID."""
    order_id = event.get("order_id")

    if not order_id:
        return _response(
            400,
            {
                "success": False,
                "error": "order_id is required",
                "error_code": "MISSING_PARAMETER",
            },
        )

    order = ORDERS.get(order_id)

    if not order:
        return _response(
            404,
            {
                "success": False,
                "error": f"Order {order_id} not found",
                "error_code": "ORDER_NOT_FOUND",
            },
        )

    return _response(200, order)


def list_orders(event: dict[str, Any]):
    """List orders for a customer."""
    customer_id = event.get("customer_id")
    limit = event.get("limit", 10)

    if not customer_id:
        return _response(
            400,
            {
                "success": False,
                "error": "customer_id is required",
                "error_code": "MISSING_PARAMETER",
            },
        )

    if customer_id not in CUSTOMERS:
        return _response(
            404,
            {
                "success": False,
                "error": f"Customer {customer_id} not found",
                "error_code": "CUSTOMER_NOT_FOUND",
            },
        )

    customer_orders = [
        {
            "order_id": o["order_id"],
            "total": o["total"],
            "status": o["status"],
            "order_date": o["order_date"],
        }
        for o in ORDERS.values()
        if o["customer_id"] == customer_id
    ]

    customer_orders.sort(key=lambda x: x["order_date"], reverse=True)
    customer_orders = customer_orders[:limit]

    return _response(200, {"customer_id": customer_id, "orders": customer_orders})


def process_refund(event: dict[str, Any]):
    """Process a refund for an order."""
    order_id = event.get("order_id")
    cents = event.get("amount_cents")
    if type(cents) is not int or not 1 <= cents <= 10000:
        return _response(400, {"error": "Expected 1 to 10000 integer cents"})
    amount = cents / 100
    reason = event.get("reason")

    if not order_id:
        return _response(
            400,
            {
                "success": False,
                "error": "order_id is required",
                "error_code": "MISSING_PARAMETER",
            },
        )

    if not reason:
        return _response(
            400,
            {
                "success": False,
                "error": "reason is required",
                "error_code": "MISSING_PARAMETER",
            },
        )

    order = ORDERS.get(order_id)
    if not order:
        return _response(
            404,
            {
                "success": False,
                "error": f"Order {order_id} not found",
                "error_code": "ORDER_NOT_FOUND",
            },
        )

    if amount > order["total"]:
        return _response(
            400,
            {
                "success": False,
                "error": f"Refund amount ${amount} exceeds order total ${order['total']}",
                "error_code": "AMOUNT_EXCEEDS_ORDER",
            },
        )

    refund_id = f"REF-{uuid.uuid4().hex[:5].upper()}"

    refund_record = {
        "refund_id": refund_id,
        "order_id": order_id,
        "amount": amount,
        "reason": reason,
        "status": "processed",
    }

    REFUNDS[refund_id] = refund_record

    return _response(
        200,
        {
            "success": True,
            **refund_record,
            "message": f"Refund of ${amount:.2f} processed. Customer will receive funds in 3-5 business days.",
        },
    )
