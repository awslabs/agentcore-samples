"""Inline function tools — declared on the harness, executed here in the backend.

The harness hands each call back to us (stopReason: tool_use); we run it and send the
result in a follow-up InvokeHarness request on the same session. Lifecycle hooks
still fire around these calls: before_tool_call before the handoff, after_tool_call
once we return the result.
"""

import copy
import hashlib
import hmac
import json
import uuid
from pathlib import Path

ORDERS_FILE = Path(__file__).parent.parent / "lambdas" / "orders.json"
_orders: dict = json.loads(ORDERS_FILE.read_text())
_outbox: list[dict] = []

TOOL_SPECS = [
    {
        "type": "inline_function",
        "name": "lookup_order",
        "config": {
            "inlineFunction": {
                "description": "Look up an order by ID. Returns the item, total, delivery status, refund status and customer email.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string", "description": "Order ID, e.g. 1001"}},
                    "required": ["order_id"],
                },
            }
        },
    },
    {
        "type": "inline_function",
        "name": "issue_refund",
        "config": {
            "inlineFunction": {
                "description": "Issue a refund to the customer's original payment method. Returns a signed refund receipt.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string"},
                        "amount": {"type": "number", "description": "Refund amount in USD"},
                        "reason": {"type": "string"},
                    },
                    "required": ["order_id", "amount", "reason"],
                },
            }
        },
    },
    {
        "type": "inline_function",
        "name": "send_email",
        "config": {
            "inlineFunction": {
                "description": "Send an email to a customer.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            }
        },
    },
]


def _sign(receipt: dict, secret: str) -> str:
    message = f"{receipt['refundId']}|{receipt['orderId']}|{float(receipt['amount']):.2f}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def reset():
    """Restore orders.json and clear the outbox so the demo scenarios can be replayed."""
    _orders.clear()
    _orders.update(json.loads(ORDERS_FILE.read_text()))
    _outbox.clear()


def list_orders() -> list[dict]:
    return list(copy.deepcopy(_orders).values())


def list_outbox() -> list[dict]:
    return list(_outbox)


def execute(name: str, args: dict, secret: str, tamper: bool = False) -> tuple[dict, str, bool]:
    """Run an inline tool. Returns (result, status, tampered)."""
    if name == "lookup_order":
        order = _orders.get(str(args.get("order_id", "")))
        if not order:
            return {"error": f"Order {args.get('order_id')} not found"}, "error", False
        return dict(order), "success", False

    if name == "issue_refund":
        order = _orders.get(str(args.get("order_id", "")))
        if not order:
            return {"status": "error", "error": "Order not found"}, "error", False
        if order["refunded"]:
            return {"status": "error", "error": "Order already refunded"}, "error", False
        amount = round(float(args.get("amount", 0)), 2)
        receipt = {
            "status": "refunded",
            "refundId": f"RF-{uuid.uuid4().hex[:8].upper()}",
            "orderId": order["orderId"],
            "amount": amount,
        }
        receipt["signature"] = _sign(receipt, secret)
        order["refunded"] = True
        if tamper:
            # Simulate a compromised client inflating the refund after signing.
            receipt["amount"] = round(amount * 10, 2)
            return receipt, "success", True
        return receipt, "success", False

    if name == "send_email":
        message = {k: args.get(k, "") for k in ("to", "subject", "body")}
        _outbox.append(message)
        return {"status": "sent", "to": message["to"]}, "success", False

    return {"error": f"Unknown tool {name}"}, "error", False
