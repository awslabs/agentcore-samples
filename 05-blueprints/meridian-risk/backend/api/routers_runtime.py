# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""`/api/assess` — invoke the KYC agent and relay its progress stream.

The Runtime emits newline-delimited JSON; this router re-frames it as SSE so the
browser can consume it with a plain fetch reader.
"""

import json
import logging
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

import aws
from models import AssessRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/assess")
def assess(request: AssessRequest):
    """Invoke the AgentCore Runtime and relay its progress stream to the UI."""
    runtime_arn = aws.require(aws.RUNTIME_ARN, "aws.RUNTIME_ARN")
    client = aws.runtime_client()

    payload = {
        "customer_id": request.customer_id,
        "assessment_type": request.assessment_type,
    }
    if request.context:
        payload["context"] = request.context

    def stream():
        # Emit before calling the Runtime. invoke_agent_runtime does not return
        # until the agent's first byte arrives, which for a full assessment is
        # tens of seconds — without this the client sees nothing at all until
        # the run is essentially over, and intermediate proxies may decide the
        # connection is idle.
        yield _sse(
            {
                "type": "status",
                "stage": "dispatch",
                "message": (
                    f"Dispatching {request.customer_id} to the AgentCore Runtime…"
                ),
            }
        )

        try:
            response = client.invoke_agent_runtime(
                agentRuntimeArn=runtime_arn,
                qualifier="DEFAULT",
                payload=json.dumps(payload).encode(),
            )
        except ClientError as exc:
            logger.exception("Runtime invocation failed")
            yield _sse({"type": "error", "message": str(exc)})
            return

        body = response.get("response")
        if body is None:
            yield _sse({"type": "error", "message": "Empty response from Runtime"})
            return

        # The Runtime streams newline-delimited JSON; re-frame it as SSE and
        # buffer partial lines across chunk boundaries.
        buffer = ""
        for chunk in body.iter_chunks():
            if not chunk:
                continue
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                for event in _parse_line(line):
                    yield _sse(event)

        for event in _parse_line(buffer):
            yield _sse(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _parse_line(line: str) -> list[dict[str, Any]]:
    """Parse one line of the Runtime's output into zero or one events."""
    line = line.strip()
    if not line:
        return []

    # The runtime SDK may already prefix lines with "data: ".
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line or line == "[DONE]":
        return []

    try:
        return [json.loads(line)]
    except json.JSONDecodeError:
        # Surface unparsable output rather than dropping it silently.
        return [{"type": "log", "message": line[:500]}]


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"
