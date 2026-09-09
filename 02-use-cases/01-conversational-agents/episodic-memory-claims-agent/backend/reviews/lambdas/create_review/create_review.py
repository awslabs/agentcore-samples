"""POST /reviews — create a review task (IAM-authed; called by the agent/backend).

Builds the canonical task record from posted structured signals + claim context.
Idempotent on task_id (== session_id): an already-RESOLVED task is never
clobbered; an OPEN task is refreshed.
"""

import json
import os
from datetime import datetime, timezone

import boto3

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

_JSON_FIELDS = ("claim", "signals", "transcript_ref", "resolution")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _encode(task):
    item = dict(task)
    for f in _JSON_FIELDS:
        if f in item:
            item[f] = json.dumps(item[f])
    return item


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
        },
        "body": json.dumps(body),
    }


def _build_task(body):
    session_id = body["session_id"]
    actor_id = body["actor_id"]
    signals = body.get("signals", {}) or {}
    policy = signals.get("policy", {}) or {}
    fraud = signals.get("fraud", {}) or {}
    coverage = signals.get("coverage", {}) or {}
    history = signals.get("claims_history", {}) or {}

    claim = {
        "policy_number": policy.get("policy_number"),
        "policy_type": policy.get("type"),
        "incident_type": coverage.get("incident_type") or fraud.get("claim_type"),
        "incident_date": fraud.get("incident_date"),
        "filing_date": fraud.get("filing_date"),
        "claimed_amount": fraud.get("claimed_amount"),
        "description": body.get("description", ""),
    }
    factual = {
        "policy": {
            "found": policy.get("found"),
            "status": policy.get("status"),
            "type": policy.get("type"),
            "deductible": policy.get("deductible"),
            "coverage_limit": policy.get("coverage_limit"),
            "exclusions": policy.get("exclusions"),
        },
        "coverage": {
            "determination": coverage.get("determination"),
            "matched_term": coverage.get("matched_term"),
            "message": coverage.get("message"),
        },
        "fraud": {
            "risk_level": fraud.get("risk_level"),
            "risk_score": fraud.get("risk_score"),
            "delay_days": fraud.get("delay_days"),
            "prior_count": fraud.get("prior_count"),
            "flags": fraud.get("flags", []),
        },
        "claims_history": {
            "prior_count": history.get("prior_count"),
            "claims": history.get("claims", []),
        },
    }
    # Pass through additional signal keys (adjudication, precedent, episodes)
    for key in ("adjudication", "precedent_patterns", "policyholder_episodes"):
        if key in signals:
            factual[key] = signals[key]
    return {
        "task_id": session_id,
        "session_id": session_id,
        "actor_id": actor_id,
        "policyholder_name": body.get("policyholder_name", ""),
        "decision_mode": body.get("decision_mode", "human"),
        "user_id": body.get("user_id"),
        "status": "OPEN",
        "created_at": _now(),
        "claim": claim,
        "signals": factual,
        "transcript_ref": {
            "memory_id": body.get("memory_id"),
            "actor_id": actor_id,
            "session_id": session_id,
        },
        "resolution": None,
    }


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        if not body.get("session_id") or not body.get("actor_id"):
            return _resp(400, {"error": "session_id and actor_id are required"})

        # Idempotency: never overwrite a resolved task.
        existing = table.get_item(Key={"task_id": body["session_id"]}).get("Item")
        if existing and existing.get("status") == "RESOLVED":
            return _resp(
                200, {"task_id": body["session_id"], "status": "RESOLVED", "note": "already resolved; not overwritten"}
            )

        task = _build_task(body)
        table.put_item(Item=_encode(task))
        return _resp(201, task)
    except Exception as e:  # noqa: BLE001 - handler boundary
        print(f"create_review error: {e}")
        return _resp(500, {"error": "Internal server error"})
