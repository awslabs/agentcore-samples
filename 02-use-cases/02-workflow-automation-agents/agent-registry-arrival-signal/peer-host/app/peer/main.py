# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AgentCore Runtime entrypoint for the peer greeter.

Serves the peer agent (the single ``greet`` skill) via the stock Strands
A2A executor. agent_factory gives each A2A context its own agent instance (the
non-deprecated isolation mode). The peer only receives A2A calls, so it needs
no arrival-aware routing.
"""

from bedrock_agentcore.runtime import serve_a2a
from model.load import load_model
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from agent_registry_sample.peer.agent import build_agent


def agent_factory(_context_id: str):
    """Build a fresh peer agent per A2A context, with the Bedrock model
    injected at deploy time."""
    return build_agent(model=load_model())


if __name__ == "__main__":
    serve_a2a(StrandsA2AExecutor(agent_factory=agent_factory))
