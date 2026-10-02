"""POST /reviews/{task_id}/resolve — adjuster decision (Cognito + adjuster group).

On resolve we do two things synchronously (Phase 5):
  1. Update the task in DynamoDB (status RESOLVED + resolution).
  2. Append an ADJUSTER decision turn to the claim's AgentCore Memory session,
     so the episode re-extracts with the human-confirmed outcome and reflections
     get grounded in human judgment. The memory write is best-effort — it never
     fails the adjuster's action.

The adjuster identity is taken from the verified token, not the request body.
Notes are length-capped. A task can only be resolved once (no re-resolving).
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import BotoCoreError, ClientError

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
_bac = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"))
_ssm = boto3.client("ssm")
MEMORY_ID_SSM_PARAM = os.environ.get("MEMORY_ID_SSM_PARAM", "/insurance-claims-demo/memory_id")

_JSON_FIELDS = ("claim", "signals", "transcript_ref", "resolution")
MAX_NOTES = 2000
VALID_DECISIONS = ("APPROVE", "DENY")


class _Dec(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
        },
        "body": json.dumps(body, cls=_Dec),
    }


def _claims(event):
    return event.get("requestContext", {}).get("authorizer", {}).get("claims", {})


def _groups(claims):
    raw = claims.get("cognito:groups", "")
    if isinstance(raw, list):
        return raw
    return raw.replace("[", "").replace("]", "").replace(",", " ").split()


def _decode(item):
    out = dict(item)
    for f in _JSON_FIELDS:
        if isinstance(out.get(f), str):
            try:
                out[f] = json.loads(out[f])
            except (ValueError, TypeError):
                pass
    return out


def _memory_id():
    try:
        return _ssm.get_parameter(Name=MEMORY_ID_SSM_PARAM)["Parameter"]["Value"]
    except (ClientError, BotoCoreError) as e:
        print(f"resolve_review: could not read memory_id from SSM: {e}")
        return ""


_CUSTOMER_MSG = {
    "APPROVE": "Good news — after review, your claim has been approved. Our team will follow up with the next steps and any payment details.",
    "DENY": "After careful review, we're unable to approve your claim at this time. Our team will follow up with the details and any options available to you.",
    "ESCALATE": "Your claim needs some additional review by a senior specialist. We'll be in touch shortly with an update.",
}


def _record_adjuster_decision(task, decision, notes):
    """Append the adjuster outcome to the claim's memory session.

    Writes TWO turns:
      1. A customer-friendly ASSISTANT outcome message — reliably extracted into
         the episode AND surfaced in the customer's chat history on next visit
         (notification). Contains no internal notes.
      2. An internal OTHER turn '[ADJUSTER DECISION] <decision> — <notes>' —
         carries the marker + adjuster notes for richer grounding; role OTHER
         keeps it out of the customer chat view.

    Best-effort: logs and returns on any failure (never fails the resolve).
    """
    try:
        ref = task.get("transcript_ref") or {}
        memory_id = ref.get("memory_id") or _memory_id()
        actor_id = task.get("actor_id")
        session_id = task.get("session_id")
        if not (memory_id and actor_id and session_id):
            print("resolve_review: missing memory refs; skipping memory write")
            return False

        internal = f"[ADJUSTER DECISION] {decision}"
        if notes:
            internal += f" — {notes}"
        customer = _CUSTOMER_MSG.get(decision, "Your claim has been reviewed.")

        # Internal grounding turn (hidden from customer chat view).
        _bac.create_event(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"content": {"text": internal}, "role": "OTHER"}}],
        )
        # Customer-facing outcome (extracted + shown on next visit).
        _bac.create_event(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"content": {"text": customer}, "role": "ASSISTANT"}}],
        )
        print(f"resolve_review: recorded adjuster decision to memory for {session_id}")
        return True
    except Exception as e:  # noqa: BLE001 - best-effort memory write; never fail the resolve
        print(f"resolve_review: memory write failed: {e}")
        return False


def handler(event, context):
    claims = _claims(event)
    if "adjuster" not in _groups(claims):
        return _resp(403, {"error": "Adjuster group required"})
    try:
        task_id = event["pathParameters"]["task_id"]
        body = json.loads(event.get("body") or "{}")
        decision = (body.get("decision") or "").strip().upper()
        notes = (body.get("notes") or "")[:MAX_NOTES]
        adjuster_id = claims.get("cognito:username") or claims.get("sub", "unknown")

        if decision not in VALID_DECISIONS:
            return _resp(400, {"error": "decision must be APPROVE or DENY"})

        existing = table.get_item(Key={"task_id": task_id}).get("Item")
        if existing is None:
            return _resp(404, {"error": f"No review task: {task_id}"})
        if existing.get("status") == "RESOLVED":
            return _resp(409, {"error": "Task already resolved"})

        resolution = {
            "decision": decision,
            "adjuster_id": adjuster_id,
            "notes": notes,
            "resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        resp = table.update_item(
            Key={"task_id": task_id},
            UpdateExpression="SET #s = :s, resolution = :r",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "RESOLVED", ":r": json.dumps(resolution)},
            ReturnValues="ALL_NEW",
        )
        task = _decode(resp["Attributes"])

        # Phase 5: ground the human decision into memory (best-effort).
        task["memory_recorded"] = _record_adjuster_decision(task, decision, notes)
        return _resp(200, task)
    except Exception as e:  # noqa: BLE001 - handler boundary
        print(f"resolve_review error: {e}")
        return _resp(500, {"error": "Internal server error"})
