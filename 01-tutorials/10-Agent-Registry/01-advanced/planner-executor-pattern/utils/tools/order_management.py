import db

def lambda_handler(event, context):
    tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    if "___" in tool_name:
        tool_name = tool_name.split("___", 1)[1]

    if tool_name == "get_order":
        order_id = event.get("order_id", "")
        order    = db.get_item("ORDERS_TABLE", {"order_id": order_id})
        if not order:
            return {"error": f"Order {order_id} not found"}
        customer = db.get_item("CUSTOMERS_TABLE", {"customer_id": order["customer_id"]}) or {}
        ships    = db.query_gsi("SHIPMENTS_TABLE", "order_id-index", "order_id", order_id)
        return {**order, "customer": customer, "shipment": ships[0] if ships else None}

    elif tool_name == "list_orders":
        email  = event.get("customer_email", "")
        status = event.get("status", "").upper()
        if email:
            customers = db.scan_filter("CUSTOMERS_TABLE", "email", email)
            if not customers:
                return {"orders": [], "count": 0}
            orders = db.scan_filter("ORDERS_TABLE", "customer_id", customers[0]["customer_id"])
        else:
            orders = db.scan_all("ORDERS_TABLE")
        if status:
            orders = [o for o in orders if o.get("status") == status]
        return {"orders": orders, "count": len(orders)}

    elif tool_name == "update_order_status":
        order_id   = event.get("order_id", "")
        new_status = event.get("status", "").upper()
        VALID      = {"PENDING","PROCESSING","SHIPPED","DELIVERED","CANCELLED","RETURNED"}
        if not db.get_item("ORDERS_TABLE", {"order_id": order_id}):
            return {"error": f"Order {order_id} not found"}
        if new_status not in VALID:
            return {"error": f"Invalid status. Must be one of {sorted(VALID)}"}
        db.update_attrs("ORDERS_TABLE", {"order_id": order_id}, {"status": new_status})
        return {"order_id": order_id, "status": new_status, "updated": True}

    elif tool_name == "update_shipping_addr":
        order_id = event.get("order_id", "")
        order    = db.get_item("ORDERS_TABLE", {"order_id": order_id})
        if not order:
            return {"error": f"Order {order_id} not found"}
        if order.get("status") in ("SHIPPED", "DELIVERED", "CANCELLED"):
            return {"error": f"Cannot update address — order is already {order['status']}"}
        addr = {k: event.get(k, "") for k in ("street", "city", "state", "zip")}
        db.update_attrs("ORDERS_TABLE", {"order_id": order_id}, {"shipping_address": addr})
        return {"order_id": order_id, "shipping_address": addr, "updated": True}

    elif tool_name == "cancel_order":
        order_id = event.get("order_id", "")
        reason   = event.get("reason", "customer_request")
        order    = db.get_item("ORDERS_TABLE", {"order_id": order_id})
        if not order:
            return {"error": f"Order {order_id} not found"}
        if order.get("status") in ("SHIPPED", "DELIVERED", "CANCELLED"):
            return {"error": f"Cannot cancel — current status is {order['status']}"}
        db.update_attrs("ORDERS_TABLE", {"order_id": order_id},
                        {"status": "CANCELLED", "cancel_reason": reason})
        pays = db.query_gsi("PAYMENTS_TABLE", "order_id-index", "order_id", order_id)
        refund_triggered = bool(pays and pays[0].get("status") == "CAPTURED")
        return {"order_id": order_id, "status": "CANCELLED",
                "reason": reason, "refund_triggered": refund_triggered}

    return {"error": f"Unknown tool: {tool_name}"}
