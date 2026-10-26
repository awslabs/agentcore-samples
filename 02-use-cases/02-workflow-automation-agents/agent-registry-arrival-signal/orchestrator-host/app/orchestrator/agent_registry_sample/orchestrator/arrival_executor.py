# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Facet 1. Deterministic arrival-event delivery for the orchestrator.

A standing agent served by ``serve_a2a`` streams every inbound A2A message
through the LLM. A registry arrival event is a system signal, not a
conversation, so routing it through the model is non-deterministic (the
model may or may not call the right tool) and
incorrect. ``ArrivalAwareExecutor`` intercepts a structured arrival DataPart
and runs the orchestrator's event path directly with no LLM in the loop, and
lets every other message fall through to the stock ``StrandsA2AExecutor`` so
ordinary agent-to-agent conversation (hello_world, search_and_invoke, free-form
chat) is unchanged.

The arrival envelope rides in an A2A DataPart shaped like::

    {"kind": "data", "data": {"arrivalEvent": {"registryRecordId": "<id>",
                                               "registryId": "<id>"}}}

Detection mirrors the SDK's own interrupt-response idiom (isinstance DataPart
+ named key), so an ordinary DataPart still reaches the base executor
unchanged. On success the result is emitted as an ``agent_response`` artifact
and the task is completed, matching the base executor's result shape
(result.artifacts[].parts[].text). On failure the task transitions to the
failed state and the exception propagates, so the caller (U1's adapter) sees a
non-success and lets SQS retry then DLQ.
"""

from __future__ import annotations

import json
import logging

from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Part, TextPart
from a2a.utils import new_task
from strands.multiagent.a2a.executor import StrandsA2AExecutor

logger = logging.getLogger()

# Key identifying a DataPart that carries a registry arrival event. Only this
# shape is treated as an arrival signal, so any other DataPart falls through
# to the base executor's normal handling.
ARRIVAL_EVENT_KEY = "arrivalEvent"


def _log(step: str, **fields: object) -> None:
    logger.info(json.dumps({"step": step, **fields}))


def _valid_arrival(payload: object) -> dict | None:
    """Strict schema check for the arrival envelope (AGENTSEC04 deterministic
    input validation).

    A valid arrival is a dict with exactly string registryRecordId + registryId
    (both non-empty) and no extra keys. Anything else (missing field, wrong
    type, extra keys that could smuggle instructions, a toolUse block) is
    rejected so a malformed or crafted DataPart never reaches the event path.
    Returns the clean payload or None.
    """
    if not isinstance(payload, dict):
        return None
    if set(payload) != {"registryRecordId", "registryId"}:
        return None
    rid, reg = payload.get("registryRecordId"), payload.get("registryId")
    if not isinstance(rid, str) or not rid or not isinstance(reg, str) or not reg:
        return None
    return {"registryRecordId": rid, "registryId": reg}


def _extract_arrival_payload(context: RequestContext) -> dict | None:
    """Return the arrival payload dict if the inbound message carries an
    arrival DataPart, else None (so the base executor handles the message).

    Mirrors StrandsA2AExecutor._extract_interrupt_responses (isinstance
    DataPart check + named key, nothing fuzzier)."""
    message = getattr(context, "message", None)
    parts = getattr(message, "parts", None) if message is not None else None
    if not parts:
        return None
    for part in parts:
        root = getattr(part, "root", part)
        data = root.data if isinstance(root, DataPart) else None
        if isinstance(data, dict) and ARRIVAL_EVENT_KEY in data:
            # Strict-validate before routing (reject malformed/crafted shapes).
            return _valid_arrival(data[ARRIVAL_EVENT_KEY])
    return None


class ArrivalAwareExecutor(StrandsA2AExecutor):
    """StrandsA2AExecutor that routes a structured arrival DataPart straight to
    the orchestrator's event path, bypassing the LLM, and delegates everything
    else to the base executor."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        arrival = _extract_arrival_payload(context)
        if arrival is None:
            # Not an arrival signal. Ordinary A2A conversation, unchanged.
            await super().execute(context, event_queue)
            return

        # Deterministic arrival path. Run U5's event path with no LLM.
        from agent_registry_sample.orchestrator.event_path import on_arrival_event_impl
        from agent_registry_sample.orchestrator.runtime_wiring import (
            build_event_path_collaborators,
        )

        record_id = arrival.get("registryRecordId")
        _log("arrival_received", registryRecordId=record_id)

        task = context.current_task
        if task is None:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            result = on_arrival_event_impl(arrival, **build_event_path_collaborators())
            text = (
                result
                if result is not None
                else ("skipped: already processed (idempotent redelivery)")
            )
            # Same shape the base executor emits on success (an agent_response
            # artifact, then complete). Keeps result.artifacts[].parts[].text.
            await updater.add_artifact([Part(root=TextPart(text=text))], name="agent_response")
            await updater.complete()
            _log("arrival_completed", registryRecordId=record_id, greet=result)
        except Exception as exc:  # noqa: BLE001 - fail the task AND propagate
            _log("arrival_failed", registryRecordId=record_id, error=str(exc))
            await updater.failed(
                message=updater.new_agent_message(
                    parts=[Part(root=TextPart(text=f"arrival handling failed: {exc}"))]
                )
            )
            raise
