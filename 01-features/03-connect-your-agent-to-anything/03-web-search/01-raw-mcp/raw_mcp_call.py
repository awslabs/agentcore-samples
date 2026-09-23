"""
Raw MCP Tool Discovery and Invocation against AgentCore gateway.

Demonstrates direct MCP protocol calls without an agent framework:
  1. Connect to the Gateway using MCP Streamable HTTP transport
  2. Call tools/list to discover the WebSearch tool and its schema
  3. Call tools/call to invoke WebSearch with a test query
  4. Display the structured results

This is useful for verifying your Gateway setup before integrating
with an agent framework.

Prerequisites:
    pip install -r ../requirements.txt
    Export environment variables from 01-setup-gateway/setup_gateway.py

Environment variables required:
    AGENTCORE_GATEWAY_URL  — Gateway MCP endpoint
    COGNITO_DOMAIN         — Cognito domain prefix
    COGNITO_CLIENT_ID      — Cognito app client ID
    COGNITO_CLIENT_SECRET  — Cognito app client secret
    COGNITO_SCOPE          — OAuth scope string

IAM permissions required:
    bedrock-agentcore:InvokeGateway (via OAuth token)

Usage:
    python raw_mcp_call.py
    python raw_mcp_call.py --query "Latest Python release"
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.gateway_auth import create_streamable_http_transport

from strands.tools.mcp.mcp_client import MCPClient

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_QUERY = "Tesla stock price right now?"


# ── Main ──────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Raw MCP tool discovery and invocation against AgentCore gateway")
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=f"Search query (default: '{DEFAULT_QUERY}')",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("AgentCore web search tool — Raw MCP Calls")
    print("=" * 60)

    # Create MCP client
    transport_factory = create_streamable_http_transport()
    mcp_client = MCPClient(transport_factory)

    with mcp_client:
        # Step 1: Discover tools
        print("\n[1] Discovering tools (tools/list)...\n")
        tools = mcp_client.list_tools_sync()
        print(f"  Found {len(tools)} tool(s):\n")

        for tool in tools:
            spec = tool.tool_spec
            print(f"  Name:        {spec['name']}")
            print(f"  Description: {spec.get('description', 'N/A')}")
            schema_str = json.dumps(spec.get("inputSchema", {}), indent=2)
            # Truncate long schemas for readability
            if len(schema_str) > 200:
                schema_str = schema_str[:200] + "..."
            print(f"  Input:       {schema_str}")
            print()

        # Step 2: Invoke WebSearch
        print("=" * 60)
        print(f"[2] Calling WebSearch: '{args.query}'")
        print("=" * 60 + "\n")

        # Find the WebSearch tool
        ws_tools = [t for t in tools if "WebSearch" in t.tool_name]
        if not ws_tools:
            print("  ERROR: WebSearch tool not found. Check your Gateway target.")
            return

        ws_tool_name = ws_tools[0].tool_name
        result = mcp_client.call_tool_sync("raw-mcp-demo", ws_tool_name, {"query": args.query})

        # Step 3: Display results
        print("[3] Results:\n")
        for content in result.get("content", result if isinstance(result, list) else [result]):
            if isinstance(content, dict) and "text" in content:
                try:
                    parsed = json.loads(content["text"])
                    print(json.dumps(parsed, indent=2))
                except (ValueError, TypeError):
                    print(content["text"][:2000])
            else:
                print(content)

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
