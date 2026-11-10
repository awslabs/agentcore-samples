# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AgentCore Memory helpers for KYC assessment history.

Memory gives the demo its cross-session story: a re-assessment of the same
corporate customer recalls what the bank concluded last time, so the agent can
comment on what changed instead of starting cold.

Layout:
  actor_id   = customer_id  (memory is scoped per corporate customer, not per
               human user — every analyst reviewing CUST003 should see the same
               assessment history)
  session_id = one assessment run
  namespaces = /kyc/{actorId}/assessments  (semantic + summary strategies)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def _client():
    return boto3.client(
        "bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )


def _memory_id() -> str:
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")
    return memory_id


def record_assessment(
    customer_id: str, session_id: str, assessment: dict[str, Any]
) -> str | None:
    """Persist a completed assessment so later runs can recall it.

    Written as a conversational turn because AgentCore Memory's extraction
    strategies (semantic, summary) operate over conversational events.

    Returns:
        The created event ID, or None if the write failed. A Memory failure must
        not fail the assessment itself — the result is still valid without it.
    """
    customer = assessment.get("customer_name", customer_id)
    recommendation = assessment.get("recommendation", "UNKNOWN")
    score = assessment.get("overall_risk_score")
    level = assessment.get("risk_level")

    credit = (assessment.get("credit_risk") or {}).get("score")
    compliance = (assessment.get("compliance") or {}).get("status")
    risks = assessment.get("key_risks") or []

    # The semantic strategy extracts facts primarily from what the USER states,
    # treating assistant turns as less authoritative. Writing the outcome as an
    # assistant reply yields useless facts ("the user requested an assessment"),
    # so state the findings on the USER turn and use the assistant turn only for
    # acknowledgement.
    outcome = (
        f"KYC onboarding assessment result for {customer} ({customer_id}), "
        f"recorded {datetime.now(timezone.utc).date().isoformat()}: "
        f"decision {recommendation}; overall risk score {score}/100 ({level} risk); "
        f"credit risk score {credit}; compliance status {compliance}."
    )
    if risks:
        # key_risks comes straight from the model, so an element may not be a
        # string (a dict or number on output drift). str() each one before
        # joining — otherwise "; ".join raises TypeError, which the caller's
        # except swallows and the whole assessment fails to persist to Memory.
        outcome += (
            " Key risks identified: "
            + "; ".join(str(risk) for risk in risks[:5])
            + "."
        )
    if assessment.get("summary"):
        outcome += f" Rationale: {assessment['summary']}"

    try:
        response = _client().create_event(
            memoryId=_memory_id(),
            actorId=customer_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[
                {
                    "conversational": {
                        "role": "USER",
                        "content": {"text": outcome},
                    }
                },
                {
                    "conversational": {
                        "role": "ASSISTANT",
                        "content": {
                            "text": (
                                f"Recorded the {recommendation} decision for "
                                f"{customer_id} in the KYC assessment history."
                            )
                        },
                    }
                },
            ],
        )
        event_id = response["event"]["eventId"]
        logger.info("[MEMORY] recorded assessment event %s", event_id)
        return event_id
    except Exception:
        logger.exception("[MEMORY] failed to record assessment for %s", customer_id)
        return None


def recall_prior_assessments(customer_id: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Retrieve a customer's prior assessments.

    Reads two layers and merges them:

    1. Long-term records extracted by the semantic strategy. These are the
       distilled facts, but extraction is asynchronous and can lag by minutes.
    2. Raw short-term events, which are durable the moment they are written.

    Relying on extraction alone means a re-assessment run seconds after the
    first one recalls nothing, so the raw events fill that window.

    Returns:
        Records shaped as `{"content": {"text": ...}, "_source": ...}`, most
        relevant or most recent first. Empty on a customer's first assessment.
    """
    client = _client()
    records: list[dict[str, Any]] = []

    try:
        response = client.retrieve_memory_records(
            memoryId=_memory_id(),
            namespace=f"/kyc/{customer_id}/assessments",
            searchCriteria={
                "searchQuery": (
                    f"prior KYC risk assessment, recommendation, and risk score "
                    f"for customer {customer_id}"
                ),
                "topK": top_k,
            },
        )
        for record in response.get("memoryRecordSummaries", []):
            content = record.get("content") or {}
            text = content.get("text") if isinstance(content, dict) else None
            if text:
                records.append({"content": {"text": text}, "_source": "extracted"})
    except Exception:
        logger.warning("[MEMORY] extracted-record recall failed for %s", customer_id)

    try:
        for event in list_assessment_events(customer_id, max_results=top_k):
            for item in event.get("payload") or []:
                conversational = item.get("conversational") or {}
                text = (conversational.get("content") or {}).get("text") or ""
                # Match the recorded outcome on either turn: record_assessment
                # puts it on the USER turn, but events written by an earlier
                # build of the agent carry it on the ASSISTANT turn.
                if "risk score" in text.lower() and "decision" in text.lower():
                    records.append({"content": {"text": text}, "_source": "event"})
                elif text.lower().startswith("kyc assessment for"):
                    records.append({"content": {"text": text}, "_source": "event"})
    except Exception:
        logger.warning("[MEMORY] event recall failed for %s", customer_id)

    # Deduplicate: an extracted fact often restates its source event.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        key = record["content"]["text"][:160]
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)

    trimmed = unique[:top_k]
    logger.info(
        "[MEMORY] recalled %d prior record(s) for %s", len(trimmed), customer_id
    )
    return trimmed


def format_prior_context(records: list[dict[str, Any]]) -> str:
    """Render recalled memories as a prompt fragment.

    Returns an empty string when there is no history, so callers can append
    unconditionally.
    """
    if not records:
        return ""

    lines = []
    for record in records:
        content = record.get("content", {})
        text = content.get("text") if isinstance(content, dict) else None
        if text:
            lines.append(f"- {text}")

    if not lines:
        return ""

    return (
        "\n\nPRIOR ASSESSMENT HISTORY for this customer (from AgentCore Memory):\n"
        + "\n".join(lines)
        + "\n\nCompare your findings against this history. Call out materially "
        "changed risk, and note if a previously identified issue is now resolved "
        "or has worsened.\n"
    )


def count_assessment_sessions(customer_id: str) -> int | None:
    """Total assessment sessions on record for a customer.

    This is the denominator for what recall actually fed the model.
    `recall_prior_assessments` caps at `top_k`, so reporting only its length
    reads as "this customer has been assessed N times" when N is really the cap
    — for a KYC demo that is a materially different claim, since how many times
    an applicant has been reviewed is itself a compliance question.

    One session is one assessment: `record_assessment` writes a single event per
    run under the runtime's session id.

    Returns:
        The session count, or None if Memory could not be read — the caller
        should then say nothing rather than imply a count of zero.
    """
    try:
        client = _client()
        total = 0
        for page in client.get_paginator("list_sessions").paginate(
            memoryId=_memory_id(), actorId=customer_id
        ):
            total += len(page.get("sessionSummaries", []))
        return total
    except Exception:
        # Never fail an assessment over a display statistic.
        logger.warning("[MEMORY] session count failed for %s", customer_id)
        return None


def list_assessment_events(customer_id: str, max_results: int = 20) -> list[dict[str, Any]]:
    """List raw Memory events for a customer, newest first.

    Powers the Memory timeline in the demo console.
    """
    try:
        client = _client()
        sessions = client.list_sessions(
            memoryId=_memory_id(), actorId=customer_id, maxResults=max_results
        ).get("sessionSummaries", [])

        events: list[dict[str, Any]] = []
        for session in sessions:
            session_events = client.list_events(
                memoryId=_memory_id(),
                actorId=customer_id,
                sessionId=session["sessionId"],
                includePayloads=True,
                maxResults=max_results,
            ).get("events", [])
            events.extend(session_events)

        # eventTimestamp is a datetime from boto3, but a field-less event would
        # default to int 0 — and sorting datetimes against an int raises
        # TypeError, which the except below would swallow, silently returning no
        # history. Sort on the ISO string with a string default so the types
        # never mix. (The API's copy already does this; keep them consistent.)
        events.sort(
            key=lambda e: str(e.get("eventTimestamp", "")), reverse=True
        )
        return events[:max_results]
    except Exception:
        logger.exception("[MEMORY] failed to list events for %s", customer_id)
        return []


def json_default(obj):
    """JSON encoder fallback for datetime values returned by boto3."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def dumps(payload: Any) -> str:
    """Serialize a payload that may contain datetimes."""
    return json.dumps(payload, default=json_default)
