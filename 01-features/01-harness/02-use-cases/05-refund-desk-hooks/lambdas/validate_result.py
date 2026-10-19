"""after_tool_call hook — verify client-supplied refund receipts.

issue_refund is an inline function: the web app's backend executes it and hands
the result back to the harness. The harness cannot know whether that result is
genuine, so this hook checks the receipt's HMAC signature against a secret the
client never shares with the model.

A deny here keeps the tool result but stops the invocation
(messageStop.stopReason == "hook_stopped").
"""

import hashlib
import hmac
import json
import os


def _sign(receipt: dict) -> str:
    message = f"{receipt['refundId']}|{receipt['orderId']}|{float(receipt['amount']):.2f}"
    return hmac.new(os.environ["RECEIPT_SECRET"].encode(), message.encode(), hashlib.sha256).hexdigest()


def _extract_receipt(tool_result: dict) -> dict | None:
    """toolResult is {"toolUseId", "status", "content": [{"text": ...} | {"json": ...}]}."""
    for block in tool_result.get("content", []):
        if isinstance(block.get("json"), dict):
            return block["json"]
        if "text" in block:
            try:
                return json.loads(block["text"])
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def lambda_handler(event, _context):
    hook_context = event.get("context", {})
    if hook_context.get("toolName") != "issue_refund":
        return {"decision": "allow", "reason": "No validation required."}

    tool_result = hook_context.get("toolResult") or {}
    if tool_result.get("truncated"):
        return {"decision": "deny", "reason": "Refund receipt was truncated; cannot verify."}

    # Calls skipped by a before_tool_call deny also reach this hook, as an error result.
    if tool_result.get("status") == "error":
        return {"decision": "allow", "reason": "Refund did not run; nothing to validate."}

    receipt = _extract_receipt(tool_result)
    if not receipt or "signature" not in receipt:
        if receipt and receipt.get("status") == "error":
            return {"decision": "allow", "reason": "Refund was rejected by the payments system."}
        return {"decision": "deny", "reason": "Refund result has no signed receipt."}

    try:
        expected = _sign(receipt)
    except (KeyError, TypeError, ValueError):
        return {"decision": "deny", "reason": "Refund receipt is malformed."}

    if not hmac.compare_digest(expected, str(receipt["signature"])):
        return {
            "decision": "deny",
            "reason": "Receipt signature mismatch: the client-supplied refund result was altered. Invocation stopped.",
        }
    return {"decision": "allow", "reason": f"Receipt {receipt['refundId']} verified."}
