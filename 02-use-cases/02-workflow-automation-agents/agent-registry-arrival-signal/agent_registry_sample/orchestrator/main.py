# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AgentCore Runtime entry point for U2's standing orchestrator agent.

Matches the `agentcore create --framework Strands --protocol A2A` scaffold
convention of the @aws/agentcore npm CLI 0.31.1. A Strands `Agent` is wrapped in
`StrandsA2AExecutor` and served via `serve_a2a`.

This is a standing AgentCore Runtime agent with its own A2A endpoint, kept
separate from U1's TriggerAdapterLambda and any other non-agent compute.
"""

from bedrock_agentcore.runtime import serve_a2a
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from agent_registry_sample.orchestrator.agent import build_agent

agent = build_agent()

if __name__ == "__main__":
    serve_a2a(StrandsA2AExecutor(agent))
