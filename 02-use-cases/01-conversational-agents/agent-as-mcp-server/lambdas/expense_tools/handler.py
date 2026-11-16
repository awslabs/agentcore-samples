"""
Mock business tools for the expense workflow, exposed as MCP tools through
Amazon Bedrock AgentCore Gateway.

One Lambda backs two MCP tools. The Gateway passes the resolved tool name in the
Lambda client context (``bedrockAgentCoreToolName``) so the handler can dispatch.
In a real deployment these functions would call your ERP, database, or ticketing
system; here they return canned data so the sample deploys with no dependencies.
"""

import json
import logging

log = logging.getLogger()
log.setLevel(logging.INFO)

EXPENSE_POLICY = {
    "meals": {"limit_usd": 75, "receipt_required_above_usd": 25},
    "travel": {"limit_usd": 500, "receipt_required_above_usd": 0},
    "office": {"limit_usd": 200, "receipt_required_above_usd": 50},
    "software": {"limit_usd": 100, "receipt_required_above_usd": 0},
}


def lookup_expense_policy(event: dict) -> dict:
    category = str(event.get("category", "")).lower().strip()
    policy = EXPENSE_POLICY.get(category)
    if policy is None:
        return {"error": f"Unknown category '{category}'. Valid: {sorted(EXPENSE_POLICY)}"}
    return {"category": category, **policy}


def record_decision(event: dict) -> dict:
    employee = event.get("employee", "unknown")
    category = event.get("category", "unknown")
    amount_usd = event.get("amount_usd", 0)
    decision = event.get("decision", "unknown")
    reason = event.get("reason", "")
    record_id = f"EXP-{abs(hash((employee, category, str(amount_usd), decision))) % 100000:05d}"
    # Mock persistence: log it (visible in CloudWatch) instead of writing to a DB.
    log.info("[expense-system] %s: %s (%s, %s, $%s) -- %s", record_id, decision, employee, category, amount_usd, reason)
    return {"record_id": record_id, "status": "recorded"}


TOOLS = {
    "lookup_expense_policy": lookup_expense_policy,
    "record_decision": record_decision,
}


def handler(event, context):
    # Gateway passes '<targetName>___<toolName>' in the client context.
    raw = ""
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    raw = custom.get("bedrockAgentCoreToolName", "")
    tool_name = raw.split("___")[-1]

    log.info("[gateway-invoke] tool=%s event=%s", tool_name, json.dumps(event))
    fn = TOOLS.get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool '{tool_name}'. Available: {sorted(TOOLS)}"}
    return fn(event or {})
