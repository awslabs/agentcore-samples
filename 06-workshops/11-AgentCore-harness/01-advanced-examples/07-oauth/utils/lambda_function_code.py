"""
Simple order management Lambda function for AgentCore Gateway target.
Exposes get_order and update_order tools.
"""

import json


# Mock order database
ORDERS = {
    "ORD-001": {
        "orderId": "ORD-001",
        "item": "Mechanical Keyboard",
        "status": "shipped",
        "amount": 149.99,
    },
    "ORD-002": {
        "orderId": "ORD-002",
        "item": "USB-C Hub",
        "status": "processing",
        "amount": 59.99,
    },
    "ORD-003": {
        "orderId": "ORD-003",
        "item": "Monitor Stand",
        "status": "delivered",
        "amount": 89.99,
    },
}


def lambda_handler(event, context):
    """Handle tool calls from AgentCore Gateway.

    The gateway passes the tool name in context.client_context.custom['bedrockAgentCoreToolName'].
    The event body contains the tool arguments directly.
    """
    import logging

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Log the raw event and context for debugging
    logger.info(f"Event: {json.dumps(event, default=str)}")
    client_context = {}
    if context and hasattr(context, "client_context") and context.client_context:
        client_context = {
            "custom": getattr(context.client_context, "custom", None),
            "env": getattr(context.client_context, "env", None),
        }
    logger.info(f"ClientContext: {json.dumps(client_context, default=str)}")

    # Get tool name from client context (set by AgentCore Gateway)
    tool_name = ""
    if context and hasattr(context, "client_context") and context.client_context:
        custom = getattr(context.client_context, "custom", None) or {}
        tool_name = custom.get("bedrockAgentCoreToolName", "")

    # Fallback: check event body for 'name' key (direct invocation / testing)
    if not tool_name:
        tool_name = event.get("name", "")

    logger.info(f"Resolved tool_name: {tool_name}")

    # The gateway prefixes tool names as "{targetName}___{toolName}".
    # Strip the prefix to get the bare tool name.
    if "___" in tool_name:
        tool_name = tool_name.split("___", 1)[1]
        logger.info(f"Stripped prefix, bare tool_name: {tool_name}")

    arguments = event.get("arguments", event)

    if tool_name == "get_order":
        order_id = arguments.get("orderId", "")
        order = ORDERS.get(order_id)
        if order:
            return {"status": "success", "result": json.dumps(order)}
        return {"status": "error", "result": f"Order {order_id} not found"}

    elif tool_name == "update_order_status":
        order_id = arguments.get("orderId", "")
        new_status = arguments.get("status", "")
        order = ORDERS.get(order_id)
        if order:
            order["status"] = new_status
            return {
                "status": "success",
                "result": json.dumps({"orderId": order_id, "newStatus": new_status}),
            }
        return {"status": "error", "result": f"Order {order_id} not found"}

    return {"status": "error", "result": f"Unknown tool: {tool_name}"}
