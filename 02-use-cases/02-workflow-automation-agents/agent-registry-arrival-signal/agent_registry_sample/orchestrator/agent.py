# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U2 orchestrator-host: the standing Strands Agents orchestrator, hosted on
AgentCore Runtime with its own A2A endpoint.

Exposes three A2A-callable tools via a single Strands `Agent`:
  - hello_world: takes no input and returns the literal string "hello world",
    which shows the orchestrator is a callable A2A agent.
  - on_arrival_event: U5's consumer event path (two-phase dedupe, batch_get
    read-back, account check + A2A greet). Its input is the payload U1's
    TriggerAdapterLambda (agent_registry_sample/infrastructure/adapter_src/handler.py)
    sends: {"registryRecordId": "<id>", "registryId": "<id>"}.
  - search_and_invoke: U6's search path, which reuses U5's verify_and_invoke
    helper.

This module defines the tools and the Strands `Agent` object. The AgentCore
Runtime / A2A server wiring (serve_a2a + StrandsA2AExecutor) lives in main.py,
following the `agentcore create --framework Strands --protocol A2A` scaffold
convention of the @aws/agentcore CLI.
"""

from __future__ import annotations

import json
import logging

from strands import Agent, tool

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _log(step: str, **fields: object) -> None:
    """One structured JSON log line."""
    logger.info(json.dumps({"step": step, **fields}))


@tool
def hello_world() -> str:
    """Return the literal string "hello world". Takes no input.

    This skill has no business logic. It shows the orchestrator is a callable
    A2A agent, not a bare function.
    """
    _log("hello_world_invoked")
    return "hello world"


@tool
def on_arrival_event(payload: dict) -> str:
    """The orchestrator's event-consumption path (U5 consumer-event-path).

    U1's TriggerAdapterLambda invokes this entry point via
    bedrock-agentcore:InvokeAgentRuntime. The input is the payload U1's
    handler.py sends, {"registryRecordId": "<id>", "registryId": "<id>"}, where
    registryRecordId is the recordId.

    Behavior: a two-phase durable dedupe claim on U1's (recordId, state)
    table, a data-plane batch_get read-back, card parse + account check (the
    shared verify_and_invoke helper, fail-closed before any send), the A2A
    greet call, and a Phase-2 COMPLETE only after the handoff succeeds.

    Cross-runtime failure propagation: any failure here propagates uncaught.
    The tool neither swallows exceptions nor returns a success on failure, so
    the orchestrator's InvokeAgentRuntime surfaces the failure to U1's adapter,
    which raises -> SQS retry -> DLQ. The collaborators (the DynamoDB table
    resource, the agent-registry data-plane client, the SigV4 invoke send
    callable, and the allowed peer accounts) are wired at invocation time by
    build_event_path_collaborators() in runtime_wiring.py.
    """
    from agent_registry_sample.orchestrator.event_path import on_arrival_event_impl
    from agent_registry_sample.orchestrator.runtime_wiring import (
        build_event_path_collaborators,
    )

    registry_record_id = payload.get("registryRecordId") if isinstance(payload, dict) else None
    _log("on_arrival_event_invoked", registryRecordId=registry_record_id)
    collaborators = build_event_path_collaborators()
    result = on_arrival_event_impl(payload, **collaborators)
    # result is the greet text on success, or None on an idempotent redelivery
    # skip. Return a non-empty string so a successful invoke carries the proof.
    # A redelivery returns the skip marker, still a successful, non-raising
    # invoke, because the arrival was already handled.
    return result if result is not None else "skipped: already processed (idempotent redelivery)"


@tool
def search_and_invoke(query: str) -> str:
    """The orchestrator's pull search path (U6 search-path).

    Searches the agent-registry data plane for a peer, selects the intended
    peer by a stable identifier (never "the first result"), and on a match runs
    the shared verify_and_invoke helper that U5 defined (account check + A2A
    greet, fail-closed before any send). The invoke uses the same injected
    SigV4 send.

    Two distinct fail-closed outcomes:
      - No peer matches -> a clean, logged, empty result (returns a non-raising
        "no peer" marker string, not an exception).
      - A peer matches but verify/invoke fails -> the shared helper raises
        (VerificationError / DriftError) and this tool lets it propagate,
        distinct from the clean "not found".

    Input is the search query string (signature search_and_invoke(query:
    str)). The registry id, allowed accounts, and SigV4 send are deploy-wired
    (build_search_path_collaborators, which reuses U5's wiring).
    """
    from agent_registry_sample.orchestrator.runtime_wiring import (
        build_search_path_collaborators,
    )
    from agent_registry_sample.orchestrator.search_path import search_and_invoke_impl

    _log("search_and_invoke_invoked", query=query)
    collaborators = build_search_path_collaborators()
    result = search_and_invoke_impl(query, **collaborators)
    # result is the greet text on a successful handoff, or None on the clean
    # fail-closed empty (no peer matched). Return a non-empty string in both
    # non-error cases so a successful tool call carries the proof. A verify or
    # invoke failure raises past here and is never turned into a success.
    return result if result is not None else "no matching peer found (clean empty result)"


SYSTEM_PROMPT = """
You are the standing orchestrator agent for the AWS Agent Registry
arrival-signal sample. You expose three tools: hello_world (a simple proof
of real agent-hood), on_arrival_event (handles a registry-approval arrival
event: dedupe, read the approved peer record back, check the peer's AWS
account, and call the peer's greet skill over A2A), and search_and_invoke
(searches the registry for a peer agent by capability and invokes it).
"""


def build_agent(model=None) -> Agent:
    """Construct the orchestrator's Strands Agent with all three tools.

    The deployed entrypoint (main.py) injects the Bedrock model via
    load_model(). build_agent() also works with no model, so unit tests can
    construct it without an AWS/model dependency.
    """
    kwargs = {
        "name": "orchestrator",
        "system_prompt": SYSTEM_PROMPT,
        "tools": [hello_world, on_arrival_event, search_and_invoke],
    }
    if model is not None:
        kwargs["model"] = model
    return Agent(**kwargs)
