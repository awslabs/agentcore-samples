"""
Web Search with a LangChain Agent.

Demonstrates a LangChain agent that uses the Web Search Tool via AgentCore gateway to answer real-time questions:
  1. Connect to the Gateway using langchain-mcp-adapters MultiServerMCPClient
  2. Discover tools from the Gateway
  3. Create a LangChain agent with ChatBedrockConverse
  4. Ask a question — the agent invokes WebSearch automatically
  5. Get a grounded response with cited sources

Prerequisites:
    pip install -r ../requirements.txt
    Export environment variables from 01-setup-gateway/setup_gateway.py
    Access to Claude Sonnet 4 in us-east-1

Environment variables required:
    AGENTCORE_GATEWAY_URL  — Gateway MCP endpoint
    COGNITO_DOMAIN         — Cognito domain prefix
    COGNITO_CLIENT_ID      — Cognito app client ID
    COGNITO_CLIENT_SECRET  — Cognito app client secret
    COGNITO_SCOPE          — OAuth scope string
    BEDROCK_MODEL_ID       — (optional) Bedrock inference profile ID or ARN;
                             defaults to us.anthropic.claude-sonnet-4-5-20250514-v1:0

IAM permissions required:
    bedrock:InvokeModel (for Claude Sonnet 4)

Usage:
    python web_search_langchain.py
    python web_search_langchain.py --query "Latest AWS announcements"
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.gateway_auth import get_oauth_token

from langchain_aws import ChatBedrockConverse
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent

# ── Configuration ─────────────────────────────────────────────────────────────

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
GATEWAY_URL = os.getenv("AGENTCORE_GATEWAY_URL", "")

DEFAULT_QUERY = "What is today's news around the world?"


# ── Agent ─────────────────────────────────────────────────────────────────────


async def run_agent(query: str):
    """Create and run a LangChain agent with Web Search tools."""
    # Get OAuth token for Gateway authentication
    token = get_oauth_token()

    # Configure the LLM
    model = ChatBedrockConverse(
        model=MODEL_ID,
        region_name=REGION,
        temperature=0.7,
        max_tokens=1024,
    )

    # Connect to the Gateway as an MCP client
    client = MultiServerMCPClient(
        {
            "web-search": {
                "transport": "streamable_http",
                "url": GATEWAY_URL,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    )
    tools = await client.get_tools()
    print(f"  Discovered {len(tools)} tool(s)")

    # Create and run the agent
    # agent = create_react_agent(model, tools=tools)
    agent = create_agent(model, tools=tools)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": query}]})

    # Print the final response
    print("\n[Agent Response]")
    print("-" * 60)
    final_message = result["messages"][-1]
    if hasattr(final_message, "content"):
        print(final_message.content)
    else:
        print(str(final_message))


# ── Main ──────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="LangChain agent with Web Search Tool via AgentCore gateway")
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=f"Search query (default: '{DEFAULT_QUERY}')",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("AgentCore web search tool — LangChain Agent")
    print("=" * 60)
    print(f"\nQuery: {args.query}\n")

    asyncio.run(run_agent(args.query))

    print("\n" + "=" * 60)
    print("Web Search with a LangChain Agent Demo Complete.!")
    print("=" * 60)


if __name__ == "__main__":
    main()
