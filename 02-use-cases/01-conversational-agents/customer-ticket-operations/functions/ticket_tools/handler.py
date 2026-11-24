"""TicketTools: the AgentCore Gateway Lambda target behind the support agent.

Four tools over the DynamoDB ticket table: ``list_tickets``, ``close_ticket``, ``add_note`` and
``confirm_close``. ``confirm_close`` changes nothing: the operator console calls it when the
operator approves a close, so the approval is recorded in the AgentCore Policy session, where
V3's temporal permit for ``close_ticket`` looks for it.
The Gateway passes the tool arguments as the event and the tool name (``TicketTools___<tool>``)
in the client context. Every call carries ``actor`` (the signed-in user's ``user_sub`` and
``customer_id``), set by the agent's runtime from the verified token, never by the model.

This function deliberately does no authorization of its own: which calls may reach it is the
Gateway's job (AgentCore Policy, per build). In the V0 build nothing stands in between.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

MAX_NOTE = 500
_table = None
_events = None


def emit(
    event: dict[str, Any], kind: str, node: str, status: str, label: str, edge: str, **extra: Any
) -> None:
    """Live-panel event: this Lambda is the only witness of the Lambda and DynamoDB hops."""
    global _events
    run_id = str(event.get("run_id", ""))[:64]
    name = os.environ.get("EVENTS_TABLE", "")
    if not run_id or not name:
        return
    try:
        if _events is None:
            import boto3

            _events = boto3.resource("dynamodb").Table(name)
        now = time.time()
        _events.put_item(
            Item={
                "run_id": run_id,
                "sk": f"{int(now * 1000):013d}#tools#{kind}",
                "ts": int(now * 1000),
                "kind": kind,
                "node": node,
                "edge": edge,
                "status": status,
                "label": label[:120],
                "source": "tools",
                "expires": int(now) + 86400,
                **extra,
            }
        )
    except Exception as exc:  # noqa: BLE001 - telemetry must never change the tool's outcome
        print(f"run event {kind} not written: {exc}")


def table():
    global _table
    if _table is None:
        import boto3

        _table = boto3.resource("dynamodb").Table(os.environ["TICKETS_TABLE"])
    return _table


def gateway_tool(context: Any) -> str:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    return str(custom.get("bedrockAgentCoreToolName", ""))


def tool_name(context: Any) -> str:
    return gateway_tool(context).split("___")[-1]


def actor_of(event: dict[str, Any]) -> dict[str, str]:
    actor = event.get("actor") or {}
    return {
        "user_sub": str(actor.get("user_sub", "")),
        "customer_id": str(actor.get("customer_id", "")),
    }


def list_tickets(tbl, event: dict[str, Any]) -> dict[str, Any]:
    customer_id = str(event.get("customer_id", ""))
    items = tbl.query(
        KeyConditionExpression="customer_id = :c",
        ExpressionAttributeValues={":c": customer_id},
    )["Items"]
    tickets = [
        {
            "ticket_id": t["ticket_id"],
            "customer_id": t["customer_id"],
            "subject": t.get("subject", ""),
            "body": t.get("body", ""),
            "status": t.get("status", "OPEN"),
            "submitted_by": t.get("submitted_by", "customer"),
            "notes": list(t.get("notes", [])),
            "origin": t.get("origin", "seed"),
        }
        for t in sorted(items, key=lambda t: t["ticket_id"])
    ]
    ids = ", ".join(t["ticket_id"] for t in tickets) or "none"
    emit(
        event,
        "store.read",
        "ddb",
        "ok",
        f"read {ids}",
        "lambda>ddb",
        tool="list_tickets",
        payload=json.dumps(
            {
                "operation": "dynamodb:Query",
                "KeyConditionExpression": "customer_id = :c",
                ":c": customer_id,
                "Items": tickets,
            },
            default=str,
        ),
    )
    return {"customer_id": customer_id, "ticket_count": len(tickets), "tickets": tickets}


def close_ticket(tbl, event: dict[str, Any]) -> dict[str, Any]:
    key = {
        "customer_id": str(event.get("customer_id", "")),
        "ticket_id": str(event.get("ticket_id", "")),
    }
    item = tbl.get_item(Key=key).get("Item")
    if item is None:
        return {**key, "status": "TARGET_NOT_FOUND"}
    if item.get("status") == "CLOSED":
        return {**key, "status": "ALREADY_CLOSED"}
    tbl.update_item(
        Key=key,
        UpdateExpression="SET #s = :closed, closed_by = :by, closed_reason = :why, closed_at = :at",
        ConditionExpression="#s <> :closed",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":closed": "CLOSED",
            ":by": actor_of(event),
            ":why": str(event.get("reason", ""))[:MAX_NOTE],
            ":at": int(time.time()),
        },
    )
    emit(
        event,
        "store.write",
        "ddb",
        "harmful",
        f"{key['ticket_id']} CLOSED",
        "lambda>ddb",
        tool="close_ticket",
        effect={"ticket_id": key["ticket_id"], "status": "CLOSED"},
        payload=json.dumps(
            {
                "operation": "dynamodb:UpdateItem",
                "Key": key,
                "UpdateExpression": "SET status = CLOSED, closed_by, closed_reason, closed_at",
                "closed_by": actor_of(event),
                "closed_reason": str(event.get("reason", ""))[:MAX_NOTE],
                "before": {"status": item.get("status", "OPEN")},
            },
            default=str,
        ),
    )
    return {**key, "status": "CLOSED"}


def add_note(tbl, event: dict[str, Any]) -> dict[str, Any]:
    key = {
        "customer_id": str(event.get("customer_id", "")),
        "ticket_id": str(event.get("ticket_id", "")),
    }
    note = str(event.get("note", "")).strip()[:MAX_NOTE]
    if not note:
        return {**key, "status": "EMPTY_NOTE"}
    if tbl.get_item(Key=key).get("Item") is None:
        return {**key, "status": "TARGET_NOT_FOUND"}
    tbl.update_item(
        Key=key,
        UpdateExpression="SET notes = list_append(if_not_exists(notes, :none), :note)",
        ExpressionAttributeValues={":none": [], ":note": [{"by": actor_of(event), "text": note}]},
    )
    emit(
        event,
        "store.write",
        "ddb",
        "ok",
        f"note on {key['ticket_id']}",
        "lambda>ddb",
        tool="add_note",
        effect={"ticket_id": key["ticket_id"], "status": "NOTED"},
        payload=json.dumps(
            {
                "operation": "dynamodb:UpdateItem",
                "Key": key,
                "UpdateExpression": "SET notes = list_append(notes, :note)",
                ":note": {"by": actor_of(event), "text": note},
            },
            default=str,
        ),
    )
    return {**key, "status": "NOTED"}


def confirm_close(tbl, event: dict[str, Any]) -> dict[str, Any]:
    key = {
        "customer_id": str(event.get("customer_id", "")),
        "ticket_id": str(event.get("ticket_id", "")),
    }
    item = tbl.get_item(Key=key).get("Item")
    if item is None:
        return {**key, "status": "TARGET_NOT_FOUND"}
    return {**key, "status": "CONFIRMED", "confirmed_by": actor_of(event)}


TOOLS = {
    "list_tickets": list_tickets,
    "close_ticket": close_ticket,
    "add_note": add_note,
    "confirm_close": confirm_close,
}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    name, event = tool_name(context), event or {}
    handler = TOOLS.get(name)
    if handler is None:
        return {"status": "UNKNOWN_TOOL"}
    ticket = event.get("ticket_id")
    emit(
        event,
        "tool.invoked",
        "lambda",
        "active",
        name + (f" {ticket}" if ticket else ""),
        "gateway>lambda",
        tool=name,
        payload=json.dumps(
            {
                "operation": "lambda:Invoke (from AgentCore Gateway)",
                "clientContext.custom.bedrockAgentCoreToolName": gateway_tool(context),
                "event": event,
            },
            default=str,
        ),
    )
    return handler(table(), event)
