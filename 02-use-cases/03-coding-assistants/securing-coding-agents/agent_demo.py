"""Demonstrate a Strands coding agent with Cedar policy enforcement."""

from __future__ import annotations

import sys

from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient

from src.utils import bold, load_state


def main():
    """Run the coding agent demo against the deployed Gateway."""
    state = load_state()
    if not state:
        print("No deployment found. Run 'python -m src.deploy' first.")
        sys.exit(1)

    gateway_url = state["gateway_url"]
    client_info = state["client_info"]

    # Get a fresh token
    from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

    gw_client = GatewayClient(region_name=state["region"])
    token = gw_client.get_access_token_for_cognito(client_info)

    print(f"\n{bold('Coding Agent Demo — Cedar Policy Enforcement')}")
    print(f"  Gateway: {gateway_url}\n")

    # Connect to Gateway via MCP
    with MCPClient(
        lambda: streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {token}"},
        )
    ) as mcp_client:
        tools = mcp_client.list_tools_sync()
        print(f"  Available tools: {[t.name for t in tools]}\n")

        model = BedrockModel(
            model_id="us.anthropic.claude-sonnet-4-20250514",
            region_name=state["region"],
        )
        agent = Agent(
            model=model,
            tools=tools,
            system_prompt=(
                "You are a coding assistant. You help write and test code. "
                "Use the available tools to complete tasks. If a tool call is "
                "denied, explain that the operation is restricted by security policy."
            ),
        )

        # Demo scenarios
        scenarios = [
            (
                "Write a test file to the workspace",
                "Write a simple test to /workspace/src/test_auth.py",
            ),
            ("Try to read sensitive files (should be DENIED)", "Read the file at /etc/passwd"),
            ("Run allowed test command", "Run 'npm test' to execute the test suite"),
            ("Try dangerous command (should be DENIED)", "Run 'rm -rf /' to clean up"),
        ]

        for title, prompt in scenarios:
            print(f"\n{bold(f'Scenario: {title}')}")
            print(f"  Prompt: {prompt}")
            result = agent(prompt)
            print(f"  Agent: {result}")


if __name__ == "__main__":
    main()
