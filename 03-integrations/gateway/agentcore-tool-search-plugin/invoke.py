"""
Create a Strands agent with AgentCoreToolSearchPlugin and run example queries.

Usage:
    python invoke.py
"""

import json
import sys

from config import AWS_REGION, MODEL_ID, STATE_FILE


def invoke():
    if not __import__("os").path.exists(STATE_FILE):
        print("[ERROR] No deployment state found. Run 'python deploy.py' first.")
        sys.exit(1)

    with open(STATE_FILE) as f:
        state = json.load(f)

    gateway_endpoint = state["gateway_endpoint"]

    print("=" * 60)
    print("Creating Strands Agent with AgentCoreToolSearchPlugin")
    print("=" * 60)
    print(f"  Gateway: {gateway_endpoint}")
    print(f"  Model:   {MODEL_ID}")
    print()

    from strands import Agent
    from strands.tools.mcp import MCPClient
    from bedrock_agentcore.gateway.integrations.strands.plugins import (
        AgentCoreToolSearchPlugin,
    )
    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

    # Connect to AgentCore Gateway via MCP
    mcp_client = MCPClient(
        lambda: aws_iam_streamablehttp_client(
            endpoint=gateway_endpoint,
            aws_region=AWS_REGION,
            aws_service="bedrock-agentcore",
        )
    )
    mcp_client.start()
    print("  [OK] MCPClient connected to AgentCore Gateway")
    print()

    # Create agent with semantic tool search plugin
    agent = Agent(plugins=[AgentCoreToolSearchPlugin(mcp_client=mcp_client)])
    print("  [OK] Agent created with AgentCoreToolSearchPlugin")
    print()

    # --- Run example queries across different travel domains ---
    examples = [
        ("Flight Search", "Find flights from San Francisco to New York next Friday"),
        ("Hotel Search", "Search for hotels in Manhattan with a pool"),
        ("Car Rental", "Find available car rentals at JFK airport for next week"),
        ("Restaurant Search", "Find Italian restaurants near Times Square with good reviews"),
        ("Currency Conversion", "Convert 500 USD to EUR and show me the current exchange rate"),
        ("Loyalty Program", "Check my loyalty points balance and what rewards I can redeem"),
    ]

    for domain, query in examples:
        print("-" * 60)
        print(f"  Domain: {domain}")
        print(f"  Query:  {query}")
        print("-" * 60)

        response = agent(query)

        # Show which tools were dynamically loaded
        tools_config = agent.tool_registry.get_all_tools_config()
        print()
        print("  ┌─ Tools loaded by semantic search ─────────────────────────")
        for tool_name in sorted(tools_config.keys()):
            print(f"  │  • {tool_name}")
        print("  └───────────────────────────────────────────────────────────")
        print()
        print(f"  Response: {response}")
        print()

    # Cleanup MCP connection
    mcp_client.__exit__(None, None, None)
    print("  [OK] MCPClient disconnected")


if __name__ == "__main__":
    invoke()
