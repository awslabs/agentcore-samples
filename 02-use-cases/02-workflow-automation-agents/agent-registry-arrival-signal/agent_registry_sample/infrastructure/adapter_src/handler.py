# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U1 trigger-adapter Lambda handler.

The handler is logic-free. It parses the SQS message's EventBridge event
envelope, extracts the Approved registry record's identifiers, and invokes the
orchestrator's event path on AgentCore Runtime via
``bedrock-agentcore:InvokeAgentRuntime``. Card reads, account verification, A2A
sessions, and dedupe decisions all live in the orchestrator (U2/U5).

The orchestrator is served over A2A, so the arrival is delivered as an A2A
``message/send`` whose message carries a single DataPart with the arrival
envelope ``{"arrivalEvent": {registryRecordId, registryId}}``. The
orchestrator's ArrivalAwareExecutor routes that DataPart straight to the event
path with no LLM. The adapter only wraps the event.

A JSON-RPC error body, or a task that ends in the ``failed`` state, counts as a
failure even though the HTTP call returned 200. The handler raises in those
cases so the message flows to SQS retry and then the DLQ, instead of clearing
the queue on a masked failure.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_ORCHESTRATOR_AGENT_RUNTIME_ARN_ENV = "ORCHESTRATOR_AGENT_RUNTIME_ARN"

# Constructed once at module scope, reused across warm Lambda invocations.
_agentcore_client = boto3.client("bedrock-agentcore")


def _log(step: str, registry_record_id: str | None, **fields: Any) -> None:
    logger.info(json.dumps({"step": step, "registryRecordId": registry_record_id, **fields}))


def on_message(event: dict, _context: Any) -> dict:
    """SQS event-source entry point. The stack sets batch_size=1, but the loop
    handles any batch size."""
    for record in event.get("Records", []):
        _handle_one_message(record)
    return {"statusCode": 200}


def _arrival_envelope(registry_record_id: str | None, registry_id: str | None) -> bytes:
    """The A2A message/send envelope carrying the arrival as a DataPart. The
    orchestrator's ArrivalAwareExecutor matches the arrivalEvent key and runs
    the event path deterministically."""
    rpc = {
        "jsonrpc": "2.0",
        "id": registry_record_id or "1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "arrivalEvent": {
                                "registryRecordId": registry_record_id,
                                "registryId": registry_id,
                            }
                        },
                    }
                ],
            }
        },
    }
    return json.dumps(rpc).encode("utf-8")


def _raise_on_failure(registry_record_id: str | None, resp: dict) -> None:
    """Raise when the InvokeAgentRuntime response body carries a JSON-RPC error
    or a failed task, even though the HTTP status was 200. A masked failure
    goes to SQS retry/DLQ instead of clearing the queue."""
    body = resp.get("response")
    raw = body.read() if hasattr(body, "read") else body
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    raw = (raw or "").strip()
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        # A non-JSON body on a 200 is unexpected for an A2A server. Treat any
        # text as best-effort proof of success and do not raise.
        return
    if isinstance(obj, dict):
        if obj.get("error"):
            raise RuntimeError(f"orchestrator returned a JSON-RPC error: {obj['error']}")
        result = obj.get("result")
        if isinstance(result, dict):
            state = (result.get("status") or {}).get("state")
            if state == "failed":
                raise RuntimeError("orchestrator task ended in the failed state")


def _handle_one_message(sqs_record: dict) -> None:
    body = sqs_record.get("body", "")
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError:
        _log("message_received", None, error="SQS message body is not valid JSON")
        raise

    detail = envelope.get("detail", {})
    registry_record_id = detail.get("registryRecordId")
    registry_id = detail.get("registryId")
    _log("message_received", registry_record_id, registryId=registry_id)

    orchestrator_agent_runtime_arn = os.environ[_ORCHESTRATOR_AGENT_RUNTIME_ARN_ENV]
    _log("invocation_attempted", registry_record_id, agentRuntimeArn=orchestrator_agent_runtime_arn)
    try:
        resp = _agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=orchestrator_agent_runtime_arn,
            payload=_arrival_envelope(registry_record_id, registry_id),
            contentType="application/json",
            accept="application/json",
        )
        _raise_on_failure(registry_record_id, resp)
    except Exception as exc:  # noqa: BLE001 (logged, then re-raised so SQS retries it)
        _log("invocation_result", registry_record_id, success=False, error=str(exc))
        raise
    _log("invocation_result", registry_record_id, success=True)
