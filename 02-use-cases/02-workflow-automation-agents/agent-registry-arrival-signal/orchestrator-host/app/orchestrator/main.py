# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AgentCore Runtime entrypoint for the orchestrator.

Serves the orchestrator agent (hello_world, on_arrival_event,
search_and_invoke) via the ArrivalAwareExecutor, which routes a structured
registry arrival DataPart straight to the event path (no LLM) and delegates
ordinary A2A conversation to the stock Strands executor. agent_factory gives
each A2A context its own agent instance (the non-deprecated isolation mode).
"""

from bedrock_agentcore.runtime import serve_a2a
from model.load import load_model

from agent_registry_sample.orchestrator.agent import build_agent
from agent_registry_sample.orchestrator.arrival_executor import ArrivalAwareExecutor


def agent_factory(_context_id: str):
    """Build a fresh orchestrator agent per A2A context, with the Bedrock model
    injected at deploy time."""
    return build_agent(model=load_model())


if __name__ == "__main__":
    serve_a2a(ArrivalAwareExecutor(agent_factory=agent_factory))
