"""
Web Search with a Strands AI Agent.

Demonstrates a Strands agent that uses the Web Search Tool via AgentCore gateway to answer real-time questions:
  1. Connect to the Gateway using MCP Streamable HTTP transport
  2. Discover the WebSearch tool via tools/list
  3. Create a Strands agent with the discovered tools
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

IAM permissions required:
    bedrock:InvokeModel (for Claude Sonnet 4)

Usage:
    python web_search_strands.py
    python web_search_strands.py --query "What are the latest AI announcements?"
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.web_search_agent import create_agent, create_mcp_client

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_QUERIES = [
    "What is today's news around the world?",
    "What are the latest developments in quantum computing?",
]


# ── Main ──────────────────────────────────────────────────────────────────────


def run_query(agent, query: str):
    """Run a single query through the agent and print the response."""
    print(f"\n{'=' * 60}")
    print(f"Query: {query}")
    print("=" * 60)

    response = agent(query)

    print("\n[Agent Response]")
    print("-" * 60)
    if hasattr(response, "message"):
        content = response.message.get("content", [])
        for block in content:
            if block.get("text"):
                print(block["text"])
    else:
        print(str(response))


def parse_args():
    parser = argparse.ArgumentParser(description="Strands agent with Web Search Tool via AgentCore gateway")
    parser.add_argument(
        "--query",
        default=None,
        help="Custom query (runs default queries if omitted)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("AgentCore web search tool — Strands Agent")
    print("=" * 60)

    mcp_client = create_mcp_client()

    with mcp_client:
        # Discover tools
        tools = mcp_client.list_tools_sync()
        print(f"\nDiscovered {len(tools)} tool(s) from Gateway")

        # Create agent
        agent = create_agent(mcp_client)

        # Run queries
        queries = [args.query] if args.query else DEFAULT_QUERIES
        for q in queries:
            run_query(agent, q)

    print("\n" + "=" * 60)
    print("Web Search with a Strands AI Agent Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
