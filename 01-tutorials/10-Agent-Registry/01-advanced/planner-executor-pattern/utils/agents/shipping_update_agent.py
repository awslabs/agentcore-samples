import os
import json
import uuid

import db
from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn
from datetime import date, timedelta

AWS_REGION  = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID    = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
RUNTIME_URL = os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")

# TABLE_NAMES_PLACEHOLDER

CARRIERS = {
    "UPS":   {"avg_days": 3, "prefix": "1Z"},
    "FedEx": {"avg_days": 2, "prefix": "FX"},
    "USPS":  {"avg_days": 5, "prefix": "94"},
    "DHL":   {"avg_days": 4, "prefix": "JD"},
}


@tool
def assign_carrier(order_id: str, weight_kg: float = 1.0, destination_state: str = "") -> str:
    """Recommend the best carrier for an order based on weight and destination.
    Args:
        order_id:          Order ID.
        weight_kg:         Package weight in kg.
        destination_state: US state code (e.g. WA).
    Returns:
        JSON with recommended carrier, days, and cost estimate.
    """
    if weight_kg > 30:
        carrier, reason = "FedEx", "heavy parcel specialist"
    elif destination_state in ("HI", "AK", "PR"):
        carrier, reason = "USPS", "best coverage for remote destinations"
    elif weight_kg < 0.5:
        carrier, reason = "USPS", "cost-optimal for lightweight packages"
    else:
        carrier, reason = "UPS", "best ground network for continental US"
    info = CARRIERS[carrier]
    cost = round(3.50 + weight_kg * 1.20 + (2.0 if destination_state in ("HI", "AK", "PR") else 0), 2)
    return json.dumps({"order_id": order_id, "recommended_carrier": carrier,
                       "reason": reason, "estimated_days": info["avg_days"],
                       "estimated_cost_usd": cost})


@tool
def create_shipment(order_id: str, carrier: str = "UPS", service: str = "GROUND") -> str:
    """Create a shipment for an order and write it to DynamoDB.
    Args:
        order_id: Order to ship (e.g. ORD-1001).
        carrier:  UPS, FedEx, USPS, or DHL.
        service:  GROUND, EXPRESS, or OVERNIGHT.
    Returns:
        JSON with shipment_id and tracking_number.
    """
    order = db.get_item("ORDERS_TABLE", {"order_id": order_id})
    if not order:
        return json.dumps({"error": f"Order {order_id} not found"})
    existing = db.query_gsi("SHIPMENTS_TABLE", "order_id-index", "order_id", order_id)
    if existing:
        return json.dumps({"error": f"Shipment already exists for order {order_id}"})
    info     = CARRIERS.get(carrier, CARRIERS["UPS"])
    days_adj = {"GROUND": 0, "EXPRESS": -1, "OVERNIGHT": info["avg_days"] - 1}
    days     = max(1, info["avg_days"] + days_adj.get(service.upper(), 0))
    est      = (date.today() + timedelta(days=days)).isoformat()
    ship_id  = f"SHIP-{str(uuid.uuid4())[:8].upper()}"
    tracking = f"{info['prefix']}{str(uuid.uuid4()).replace('-','')[:16].upper()}"
    item     = {"shipment_id": ship_id, "order_id": order_id, "carrier": carrier,
                "service": service.upper(), "tracking_number": tracking,
                "status": "CREATED", "estimated_delivery": est,
                "note": "[DEMO] Shipment not actually created"}
    db.put_item("SHIPMENTS_TABLE", item)
    db.update_attrs("ORDERS_TABLE", {"order_id": order_id}, {"status": "SHIPPED"})
    return json.dumps(item)


@tool
def update_shipment_status(shipment_id: str, status: str) -> str:
    """Update the status of an existing shipment.
    Args:
        shipment_id: Shipment ID from create_shipment.
        status:      New status: CREATED, IN_TRANSIT, OUT_FOR_DELIVERY, DELIVERED, EXCEPTION.
    Returns:
        JSON confirming the update.
    """
    VALID = {"CREATED", "IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED", "EXCEPTION"}
    shipment = db.get_item("SHIPMENTS_TABLE", {"shipment_id": shipment_id})
    if not shipment:
        return json.dumps({"error": f"Shipment {shipment_id} not found"})
    if status.upper() not in VALID:
        return json.dumps({"error": f"Invalid status. Must be one of {sorted(VALID)}"})
    db.update_attrs("SHIPMENTS_TABLE", {"shipment_id": shipment_id}, {"status": status.upper()})
    return json.dumps({"shipment_id": shipment_id, "status": status.upper(), "updated": True})


model  = BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION)
agent  = Agent(
    model=model,
    tools=[assign_carrier, create_shipment, update_shipment_status],
    system_prompt=(
        "You are a shipping coordinator. Use assign_carrier to pick the best carrier, "
        "then create_shipment to book it, then update_shipment_status to confirm."
    ),
    name="ShippingUpdateAgent",
    description="Creates shipments with carrier selection and status tracking.",
)

server = A2AServer(agent=agent, http_url=RUNTIME_URL, serve_at_root=True)

app = FastAPI()
app.mount("/", server.to_fastapi_app())

@app.get("/ping")
def ping():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "9000")))  # nosec B104
