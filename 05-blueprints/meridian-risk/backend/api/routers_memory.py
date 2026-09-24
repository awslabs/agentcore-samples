# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""`/api/memory/*` — assessment history for one corporate customer."""

import json
import logging
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter

import aws

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory")


@router.get("/{customer_id}")
def get_memory(customer_id: str) -> dict[str, Any]:
    """Return the assessment timeline and extracted long-term records.

    `events` is short-term memory (raw assessment turns). `records` is what the
    semantic and summary strategies extracted — these appear a minute or two
    after an assessment, once extraction has run.
    """
    memory_id = aws.require(aws.MEMORY_ID, "aws.MEMORY_ID")
    client = aws.client("bedrock-agentcore")
    customer_id = customer_id.upper()

    events: list[dict] = []
    try:
        sessions = client.list_sessions(
            memoryId=memory_id, actorId=customer_id, maxResults=50
        ).get("sessionSummaries", [])

        for session in sessions:
            session_events = client.list_events(
                memoryId=memory_id,
                actorId=customer_id,
                sessionId=session["sessionId"],
                includePayloads=True,
                maxResults=50,
            ).get("events", [])
            events.extend(session_events)

        events.sort(key=lambda e: str(e.get("eventTimestamp", "")), reverse=True)
    except ClientError as exc:
        logger.warning("Memory event listing failed for %s: %s", customer_id, exc)

    records: list[dict] = []
    try:
        records = client.retrieve_memory_records(
            memoryId=memory_id,
            namespace=f"/kyc/{customer_id}/assessments",
            searchCriteria={
                "searchQuery": f"KYC risk assessment for {customer_id}",
                "topK": 10,
            },
        ).get("memoryRecordSummaries", [])
    except ClientError as exc:
        # Normal before extraction has run for the first time.
        logger.info("No extracted records yet for %s: %s", customer_id, exc)

    return {
        "customer_id": customer_id,
        "memory_id": memory_id,
        "session_count": len({e.get("sessionId") for e in events if e.get("sessionId")}),
        "event_count": len(events),
        "events": json.loads(json.dumps(events, default=str)),
        "records": json.loads(json.dumps(records, default=str)),
    }


# Register the secured routes. Must follow every @secured declaration above.
