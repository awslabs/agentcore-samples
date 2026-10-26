"""Ticket event handler: SNS -> validate -> DDB/dispatch -> invoke Runtime.

Supports two payload formats:
  1. Full ticket (DDB mock): {ticket_id, requester_id, title, description, priority}
     - Validates, persists to DDB, invokes Runtime with full payload.
  2. Jira issue key: {issue_key, requester_id}
     - Thin pass-through; Jira is the system of record.
     - Does NOT persist to DDB (the agent reads from Jira via MCP).

The handler detects the mode based on the presence of `issue_key` vs `ticket_id`.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TICKETS_TABLE = os.environ.get("TICKETS_TABLE", "")
AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]

# Validate that the runtime ARN was properly wired by CDK (not left as placeholder)
if AGENT_RUNTIME_ARN == "PENDING":
    raise RuntimeError(
        "AGENT_RUNTIME_ARN is 'PENDING' — CDK did not wire the Runtime ARN. "
        "Ensure the parent stack sets this env var after AgentCore constructs are created."
    )

_REGION = os.environ.get("AWS_REGION", "us-west-2")
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_agentcore = boto3.client("bedrock-agentcore", region_name=_REGION)

# Full-ticket mode requires these fields
REQUIRED_TICKET_FIELDS = {"ticket_id", "title", "description", "requester_id"}
# Issue-key mode requires these fields
# Note: requester_id is optional for issue-key mode — defaults to the issue_key
# itself, which means Memory will namespace by issue rather than by user. Include
# requester_id in the payload for proper per-user incident tracking.
REQUIRED_ISSUE_FIELDS = {"issue_key"}


def _is_jira_mode(payload: dict) -> bool:
    """Detect whether this is a Jira issue-key payload vs full ticket."""
    return "issue_key" in payload and "ticket_id" not in payload


def _validate_ticket(ticket: dict) -> str | None:
    """Returns error message if invalid, None if valid."""
    missing = REQUIRED_TICKET_FIELDS - set(ticket.keys())
    if missing:
        return f"Missing required fields: {sorted(missing)}"
    return None


def _validate_issue(payload: dict) -> str | None:
    """Returns error message if invalid, None if valid."""
    missing = REQUIRED_ISSUE_FIELDS - set(payload.keys())
    if missing:
        return f"Missing required fields: {sorted(missing)}"
    return None


def _persist_ticket(ticket: dict) -> None:
    """Idempotent write to DynamoDB (full-ticket mode only).

    Sanitizes the payload to only persist expected fields, preventing
    unexpected attributes from being written to the table.
    """
    if not TICKETS_TABLE:
        logger.warning("TICKETS_TABLE not set — skipping DDB persist")
        return

    # Whitelist allowed fields to prevent unexpected attribute injection
    ALLOWED_FIELDS = {
        "ticket_id",
        "title",
        "description",
        "requester_id",
        "priority",
        "category",
        "status",
        "created_at",
        "updated_at",
    }
    sanitized = {k: v for k, v in ticket.items() if k in ALLOWED_FIELDS}

    tickets_table = _ddb.Table(TICKETS_TABLE)
    ticket_id = sanitized["ticket_id"]
    now = datetime.now(timezone.utc).isoformat()

    sanitized.setdefault("priority", "MEDIUM")
    sanitized.setdefault("status", "Open")
    sanitized.setdefault("created_at", now)

    try:
        tickets_table.put_item(
            Item=sanitized,
            ConditionExpression="attribute_not_exists(ticket_id)",
        )
        logger.info("Persisted ticket %s", ticket_id)
    except _ddb.meta.client.exceptions.ConditionalCheckFailedException:
        logger.info("Ticket %s already exists, skipping DDB write", ticket_id)


def _invoke_runtime(payload: dict, event_id: str) -> None:
    """Invoke AgentCore Runtime with the payload."""
    # runtimeSessionId must be at least 33 characters
    session_id = f"event-{event_id}-{uuid.uuid4().hex}"
    try:
        _agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=json.dumps(payload).encode("utf-8"),
        )
        logger.info("Invoked runtime (session=%s)", session_id)
    except Exception:
        logger.exception("Failed to invoke runtime for %s", event_id)
        raise


def lambda_handler(event, context):
    """Process SNS event containing a ticket or issue-key payload.

    Processes all records in the batch, collecting failures. If any record
    fails, the function raises after processing all records so that the
    DLQ captures the entire event (SNS delivers one record per invocation
    in practice, but this handles edge cases gracefully).
    """
    # STEP: TRIGGER — Event arrives from external system via SNS
    logger.info("Trigger received event")

    failures: list[str] = []

    for record in event.get("Records", []):
        sns_msg = record.get("Sns", {}).get("Message", "{}")
        try:
            payload = json.loads(sns_msg)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in SNS message: %s", e)
            failures.append(f"JSON parse error: {e}")
            continue

        try:
            if _is_jira_mode(payload):
                # ─── Jira issue-key mode ─────────────────────────────
                # Thin pass-through: no DDB persist, Jira is system of record.
                error = _validate_issue(payload)
                if error:
                    logger.error("Validation failed for issue event: %s", error)
                    failures.append(f"Invalid issue payload: {error}")
                    continue

                issue_key = payload["issue_key"]
                # Ensure requester_id has a fallback for memory actor_id
                payload.setdefault("requester_id", issue_key)
                logger.info("Dispatching Jira issue %s (requester=%s)", issue_key, payload["requester_id"])
                _invoke_runtime(payload, issue_key)

            else:
                # ─── Full-ticket mode (DDB mock) ─────────────────────
                error = _validate_ticket(payload)
                if error:
                    logger.error("Validation failed for ticket: %s", error)
                    failures.append(f"Invalid ticket payload: {error}")
                    continue

                ticket_id = payload["ticket_id"]
                logger.info("Processing ticket %s (priority=%s)", ticket_id, payload.get("priority"))
                _persist_ticket(payload)
                _invoke_runtime(payload, ticket_id)

        except Exception as exc:
            record_id = payload.get("ticket_id") or payload.get("issue_key") or "unknown"
            logger.exception("Failed to process record %s", record_id)
            failures.append(f"{record_id}: {type(exc).__name__}: {exc}")

    if failures:
        # Raise so the Lambda reports failure and the event goes to the DLQ
        raise RuntimeError(f"{len(failures)} record(s) failed: {'; '.join(failures)}")

    return {"statusCode": 200, "body": "OK"}
