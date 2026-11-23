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
def get_order_payment_info(order_id: str) -> str:
    """Look up order and its payment before deciding refund amount.
    Args:
        order_id: The order ID (e.g. ORD-1001).
    Returns:
        JSON with order status and payment details.
    """
    order = db.get_item("ORDERS_TABLE", {"order_id": order_id})
    if not order:
        return json.dumps({"error": f"Order {order_id} not found"})
    pays = db.query_gsi("PAYMENTS_TABLE", "order_id-index", "order_id", order_id)
    return json.dumps({"order_id": order_id, "order_status": order["status"],
                       "payment": pays[0] if pays else None})


@tool
def issue_refund(order_id: str, amount: float, reason: str = "customer_request") -> str:
    """Issue a refund after validating the order and payment status.
    Args:
        order_id: Order to refund (e.g. ORD-1001).
        amount:   Refund amount in USD.
        reason:   Reason for refund.
    Returns:
        JSON with refund_id and status.
    """
    order = db.get_item("ORDERS_TABLE", {"order_id": order_id})
    if not order:
        return json.dumps({"error": f"Order {order_id} not found"})
    pays = db.query_gsi("PAYMENTS_TABLE", "order_id-index", "order_id", order_id)
    if not pays:
        return json.dumps({"error": "No payment found for order"})
    pay = pays[0]
    if pay["status"] == "REFUNDED":
        return json.dumps({"error": "Order already fully refunded"})
    if pay["status"] == "PENDING":
        return json.dumps({"error": "Payment not yet captured"})
    if amount > pay["amount"]:
        return json.dumps({"error": f"Refund ${amount} exceeds captured ${pay['amount']}"})
    refund_id = f"REF-{str(uuid.uuid4())[:8].upper()}"
    item = {"refund_id": refund_id, "order_id": order_id, "payment_id": pay["payment_id"],
            "gateway": pay["gateway"], "amount": amount, "reason": reason,
            "status": "COMPLETED", "note": "[DEMO] Refund not actually processed"}
    db.put_item("REFUNDS_TABLE", item)
    return json.dumps(item)


@tool
def get_refund_status(refund_id: str) -> str:
    """Check the status of a previously issued refund.
    Args:
        refund_id: The refund ID (e.g. REF-AB12CD34).
    Returns:
        JSON with refund details.
    """
    refund = db.get_item("REFUNDS_TABLE", {"refund_id": refund_id})
    if not refund:
        return json.dumps({"error": f"Refund {refund_id} not found"})
    return json.dumps(refund)


model  = BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION)
agent  = Agent(
    model=model,
    tools=[get_order_payment_info, issue_refund, get_refund_status],
    system_prompt=(
        "You are a payment refund specialist. Always call get_order_payment_info first "
        "to verify the order and payment before issuing a refund. "
        "Confirm the refund by calling get_refund_status after issue_refund."
    ),
    name="PaymentRefundAgent",
    description="Issues refunds with multi-step validation: verify order/payment, issue refund, confirm status.",
)

server = A2AServer(agent=agent, http_url=RUNTIME_URL, serve_at_root=True)

app = FastAPI()
app.mount("/", server.to_fastapi_app())

@app.get("/ping")
def ping():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "9000")))  # nosec B104
