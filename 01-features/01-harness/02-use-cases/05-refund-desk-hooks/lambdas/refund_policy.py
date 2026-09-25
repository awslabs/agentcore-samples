"""before_tool_call hook — enforce refund and email policy before a tool runs.

A deny here skips only this tool call; the agent loop continues and the model
sees that the call was blocked, so it can explain or escalate.
"""

import json
from pathlib import Path

ORDERS = json.loads((Path(__file__).parent / "orders.json").read_text())
AUTO_APPROVE_LIMIT = 500.00


def _tool_input(hook_context: dict) -> dict | None:
    raw = hook_context.get("toolInput")
    if isinstance(raw, dict) and raw.get("truncated"):
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw or {}


def _check_refund(args: dict) -> tuple[str, str]:
    order = ORDERS.get(str(args.get("order_id", "")))
    if not order:
        return "deny", f"Order {args.get('order_id')} does not exist."
    if order["refunded"]:
        return "deny", f"Order {order['orderId']} was already refunded."
    if order["status"] != "delivered":
        return "deny", f"Order {order['orderId']} is '{order['status']}'; only delivered orders can be refunded."

    try:
        amount = float(args.get("amount", 0))
    except (TypeError, ValueError):
        return "deny", "Refund amount is not a number."
    if amount <= 0:
        return "deny", "Refund amount must be positive."
    if amount > order["total"]:
        return "deny", f"Refund ${amount:.2f} exceeds the order total ${order['total']:.2f}."
    if amount > AUTO_APPROVE_LIMIT:
        return "deny", (
            f"Refund ${amount:.2f} exceeds the ${AUTO_APPROVE_LIMIT:.0f} auto-approval limit. "
            "Escalate to a human supervisor."
        )
    return "allow", f"Refund ${amount:.2f} for order {order['orderId']} is within policy."


def _check_email(args: dict) -> tuple[str, str]:
    recipient = str(args.get("to", "")).strip().lower()
    known = {order["email"] for order in ORDERS.values()}
    if recipient not in known:
        return "deny", f"'{recipient}' is not a customer email on file. Emails may only go to customers."
    return "allow", f"Recipient {recipient} is a customer on file."


def lambda_handler(event, _context):
    hook_context = event.get("context", {})
    tool_name = hook_context.get("toolName")

    if tool_name not in ("issue_refund", "send_email"):
        return {"decision": "allow", "reason": f"No policy for '{tool_name}'."}

    args = _tool_input(hook_context)
    if args is None:
        return {"decision": "deny", "reason": "Tool input was truncated or unreadable; cannot evaluate policy."}

    check = _check_refund if tool_name == "issue_refund" else _check_email
    decision, reason = check(args)
    return {"decision": decision, "reason": reason}
