#!/usr/bin/env python3
"""
Test script for deployed MCP server
Uses the MCP Python client library to properly communicate with the server
"""

import asyncio
import sys
from datetime import timedelta
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def test_mcp_server(agent_arn, bearer_token, region):
    """Test the deployed MCP server."""

    # Encode the ARN for URL
    encoded_arn = agent_arn.replace(":", "%3A").replace("/", "%2F")
    mcp_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

    headers = {
        "authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }

    print(f"Connecting to: {mcp_url}")
    print()

    try:
        async with streamablehttp_client(
            mcp_url, headers, timeout=timedelta(seconds=120), terminate_on_close=False
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                print("🔄 Initializing MCP session...")
                await session.initialize()
                print("✓ MCP session initialized\n")

                print("🔄 Listing available tools...")
                tool_result = await session.list_tools()

                print("\n📋 Available MCP Tools:")
                print("=" * 50)
                for tool in tool_result.tools:
                    print(f"🔧 {tool.name}: {tool.description}")

                print("\n🧪 Testing MCP Tools:")
                print("=" * 50)

                # Test add_numbers
                print("\n➕ Testing add_numbers(5, 3)...")
                add_result = await session.call_tool(name="add_numbers", arguments={"a": 5, "b": 3})
                print(f"   Result: {add_result.content[0].text}")

                # Test multiply_numbers
                print("\n✖️  Testing multiply_numbers(4, 7)...")
                multiply_result = await session.call_tool(name="multiply_numbers", arguments={"a": 4, "b": 7})
                print(f"   Result: {multiply_result.content[0].text}")

                # Test greet_user
                print("\n👋 Testing greet_user('Alice')...")
                greet_result = await session.call_tool(name="greet_user", arguments={"name": "Alice"})
                print(f"   Result: {greet_result.content[0].text}")

                print("\n✅ MCP tool testing completed!")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def main():
    if len(sys.argv) != 4:
        print("Usage: python test_mcp_server.py <agent_arn> <bearer_token> <region>")
        print("\nExample:")
        print("  python test_mcp_server.py arn:aws:bedrock-agentcore:... eyJraWQiOiJ... us-west-2")
        sys.exit(1)

    agent_arn = sys.argv[1]
    bearer_token = sys.argv[2]
    region = sys.argv[3]

    asyncio.run(test_mcp_server(agent_arn, bearer_token, region))


if __name__ == "__main__":
    main()
