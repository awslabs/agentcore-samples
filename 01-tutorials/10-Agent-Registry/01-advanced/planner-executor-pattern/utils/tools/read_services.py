import db

def lambda_handler(event, context):
    tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    if "___" in tool_name:
        tool_name = tool_name.split("___", 1)[1]

    if tool_name == "get_payment_status":
        order_id   = event.get("order_id", "")
        payment_id = event.get("payment_id", "")
        if order_id:
            items = db.query_gsi("PAYMENTS_TABLE", "order_id-index", "order_id", order_id)
            payment = items[0] if items else None
        elif payment_id:
            payment = db.get_item("PAYMENTS_TABLE", {"payment_id": payment_id})
        else:
            return {"error": "Provide order_id or payment_id"}
        return payment if payment else {"error": "Payment not found"}

    elif tool_name == "check_inventory":
        sku  = event.get("sku", "")
        item = db.get_item("INVENTORY_TABLE", {"sku": sku})
        if not item:
            return {"error": f"SKU '{sku}' not found"}
        return {**item, "available": item["stock"] - item["reserved"]}

    elif tool_name == "check_multiple_inventory":
        results = []
        for sku in event.get("skus", []):
            item = db.get_item("INVENTORY_TABLE", {"sku": sku})
            results.append({**item, "available": item["stock"] - item["reserved"]}
                           if item else {"sku": sku, "error": "not found"})
        return {"items": results, "count": len(results)}

    elif tool_name == "track_shipment":
        shipment_id = event.get("shipment_id", "")
        order_id    = event.get("order_id", "")
        if shipment_id:
            shipment = db.get_item("SHIPMENTS_TABLE", {"shipment_id": shipment_id})
        elif order_id:
            items    = db.query_gsi("SHIPMENTS_TABLE", "order_id-index", "order_id", order_id)
            shipment = items[0] if items else None
        else:
            return {"error": "Provide shipment_id or order_id"}
        return shipment if shipment else {"error": "Shipment not found — order may not have shipped yet"}

    elif tool_name == "estimate_delivery":
        order_id = event.get("order_id", "")
        order    = db.get_item("ORDERS_TABLE", {"order_id": order_id})
        if not order:
            return {"error": f"Order {order_id} not found"}
        items    = db.query_gsi("SHIPMENTS_TABLE", "order_id-index", "order_id", order_id)
        shipment = items[0] if items else None
        if shipment:
            return {"order_id": order_id, "order_status": order["status"],
                    "estimated_delivery": shipment["estimated_delivery"],
                    "carrier": shipment["carrier"], "tracking": shipment["tracking_number"]}
        return {"order_id": order_id, "order_status": order["status"],
                "estimated_delivery": None, "note": "Shipment not yet created"}

    return {"error": f"Unknown tool: {tool_name}"}
