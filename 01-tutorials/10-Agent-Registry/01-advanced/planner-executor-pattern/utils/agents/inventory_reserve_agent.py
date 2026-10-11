import os
import json
import uuid

import db
from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn

AWS_REGION  = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID    = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
RUNTIME_URL = os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")

# TABLE_NAMES_PLACEHOLDER


@tool
def reserve_inventory(order_id: str, sku: str, quantity: int) -> str:
    """Reserve inventory units for an order.
    Args:
        order_id: Order this reservation is for (e.g. ORD-1001).
        sku:      Product SKU (e.g. WIDGET-42).
        quantity: Units to reserve.
    Returns:
        JSON with reservation_id and updated stock.
    """
    item = db.get_item("INVENTORY_TABLE", {"sku": sku})
    if not item:
        return json.dumps({"error": f"SKU '{sku}' not found"})
    available = item["stock"] - item["reserved"]
    if quantity > available:
        return json.dumps({"error": f"Insufficient stock: requested {quantity}, available {available}"})
    db.update_attrs("INVENTORY_TABLE", {"sku": sku},
                    {"reserved": item["reserved"] + quantity})
    res_id = f"RES-{str(uuid.uuid4())[:8].upper()}"
    record = {"reservation_id": res_id, "order_id": order_id, "sku": sku,
              "quantity": quantity, "status": "ACTIVE"}
    db.put_item("RESERVATIONS_TABLE", record)
    return json.dumps({**record, "remaining_available": available - quantity})


@tool
def release_reservation(reservation_id: str) -> str:
    """Release a reservation and return units to available stock.
    Args:
        reservation_id: Reservation ID from reserve_inventory.
    Returns:
        JSON confirming release.
    """
    res = db.get_item("RESERVATIONS_TABLE", {"reservation_id": reservation_id})
    if not res:
        return json.dumps({"error": f"Reservation {reservation_id} not found"})
    if res["status"] == "RELEASED":
        return json.dumps({"error": "Already released"})
    item = db.get_item("INVENTORY_TABLE", {"sku": res["sku"]})
    if item:
        db.update_attrs("INVENTORY_TABLE", {"sku": res["sku"]},
                        {"reserved": max(0, item["reserved"] - res["quantity"])})
    db.update_attrs("RESERVATIONS_TABLE", {"reservation_id": reservation_id},
                    {"status": "RELEASED"})
    return json.dumps({"reservation_id": reservation_id, "status": "RELEASED",
                       "sku": res["sku"], "quantity_released": res["quantity"]})


@tool
def get_reservation_status(reservation_id: str) -> str:
    """Check reservation status and current stock levels.
    Args:
        reservation_id: Reservation ID.
    Returns:
        JSON with reservation and stock details.
    """
    res = db.get_item("RESERVATIONS_TABLE", {"reservation_id": reservation_id})
    if not res:
        return json.dumps({"error": f"Reservation {reservation_id} not found"})
    item = db.get_item("INVENTORY_TABLE", {"sku": res["sku"]}) or {}
    return json.dumps({**res, "current_stock": item.get("stock", 0),
                       "current_reserved": item.get("reserved", 0)})


model  = BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION)
agent  = Agent(
    model=model,
    tools=[reserve_inventory, release_reservation, get_reservation_status],
    system_prompt=(
        "You are an inventory reservation specialist. Check stock before reserving. "
        "Support rollback via release_reservation if downstream steps fail."
    ),
    name="InventoryReserveAgent",
    description="Reserves inventory units for orders with rollback support.",
)

server = A2AServer(agent=agent, http_url=RUNTIME_URL, serve_at_root=True)

app = FastAPI()
app.mount("/", server.to_fastapi_app())

@app.get("/ping")
def ping():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "9000")))  # nosec B104
