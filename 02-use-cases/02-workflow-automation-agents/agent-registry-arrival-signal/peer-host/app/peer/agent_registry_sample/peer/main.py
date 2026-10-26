# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AgentCore Runtime entry point for U3's peer greeter agent.

Matches the `agentcore create --framework Strands --protocol A2A` scaffold
convention of the @aws/agentcore npm CLI 0.31.1. A Strands `Agent` is wrapped
in `StrandsA2AExecutor` and served via `serve_a2a`.

The peer serves its A2A 0.3 card (build_peer_card) at its invocation
endpoint. U4 publishes that card into the registry record, and U5/U6 read it
back (account-checked, fail-closed) before calling greet. The card is
unsigned in this sample (see agent_registry_sample.peer.card).

The peer never self-registers, so there is no registration call
here. Its execution role carries zero registry permissions.
"""

from bedrock_agentcore.runtime import serve_a2a
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from agent_registry_sample.peer.agent import build_agent

agent = build_agent()

if __name__ == "__main__":
    serve_a2a(StrandsA2AExecutor(agent))
