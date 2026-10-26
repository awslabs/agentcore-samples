"""Strands Agent entrypoint for AgentCore Runtime deployment.

This file is the entrypoint used by `agentcore deploy`. It defines
the coding assistant agent with tools that are gated by Cedar policies
at the Gateway level.
"""

from strands import Agent
from strands.models.bedrock import BedrockModel


def create_agent():
    """Create the coding assistant agent."""
    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514",
        region_name="us-west-2",
    )

    agent = Agent(
        model=model,
        system_prompt=(
            "You are a coding assistant. You help developers write, review, "
            "and debug code. You have access to file system, shell, and code "
            "execution tools. All tool calls are governed by Cedar policies "
            "enforced at the AgentCore Gateway — some actions may be denied "
            "based on security policies."
        ),
    )
    return agent


# AgentCore Runtime expects a module-level agent instance
agent = create_agent()
