#!/usr/bin/env python3

import argparse
import asyncio
import logging
import sys
import traceback
from bedrock_agentcore.identity.auth import requires_access_token
from datetime import timedelta
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.tools.mcp import MCPClient

from utils import get_ssm_parameter

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

gateway_access_token = None


@requires_access_token(
    provider_name=get_ssm_parameter("/app/customersupportvpc/gateway/oauth2_provider_name"),
    scopes=[],  # Optional unless required
    auth_flow="M2M",
)
async def _get_access_token_manually(access_token: str):
    global gateway_access_token
    gateway_access_token = access_token
    return access_token


async def connect_to_gateway(gateway_url: str, prompt: str):
    """Connect to the gateway and send a prompt"""

    print(f"🔗 Gateway URL: {gateway_url}")
    print(gateway_access_token)
    # Set up MCP client
    client = MCPClient(
        lambda: streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {gateway_access_token}"},
            timeout=timedelta(seconds=120),
        )
    )

    try:
        with client:
            print("✅ Connected to gateway")

            # List available tools
            print("\n🔄 Listing available tools...")
            tools = client.list_tools_sync()

            print("\n📋 Available Gateway Tools:")
            print("=" * 50)
            for tool in tools:
                print(f"🔧 {tool.tool_name}")
                # print(f"   Description: {tool.description}")
                if hasattr(tool, "input_schema") and tool.input_schema:
                    properties = tool.input_schema.get("properties", {})
                    if properties:
                        print(f"   Parameters: {list(properties.keys())}")
                print()

            print(f"✅ Found {len(tools)} tools available.")

            # Create agent with tools and send prompt
            print("\n🤖 Sending prompt to agent...")
            print(f"📝 Prompt: {prompt}")
            print("=" * 50)

            print("\n🤖 Agent Response:")
            print("=" * 50)
            agent = Agent(tools=tools)
            agent(prompt)

    except Exception as e:
        logger.error(f"Error connecting to gateway: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        print(f"❌ Error: {e}")
        sys.exit(1)


def main():
    """CLI tool to interact with Gateway using a prompt."""

    parser = argparse.ArgumentParser(description="Gateway MCP CLI Tool")
    parser.add_argument("--prompt", "-p", required=True, help="Prompt to send to the gateway agent")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Set logging level based on arguments
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    else:
        logging.getLogger().setLevel(logging.WARNING)

    print("🚀 Gateway MCP CLI Tool")
    print("=" * 30)

    # Fetch access token first
    print("🔐 Acquiring OAuth2 access token...")
    try:
        asyncio.run(_get_access_token_manually(access_token=""))
        print("✅ Access token acquired successfully")
    except Exception as e:
        logger.error(f"Failed to acquire access token: {e}")
        print(f"❌ Failed to acquire access token: {e}")
        sys.exit(1)

    # Get gateway URL from SSM Parameter Store
    try:
        gateway_url = get_ssm_parameter("/app/customersupportvpc/gateway/gateway_url")
        print(f"🌐 Gateway URL: {gateway_url}")
    except Exception as e:
        logger.error(f"Error reading gateway URL: {e}")
        print(f"❌ Error reading gateway URL from SSM: {str(e)}")
        sys.exit(1)

    # Connect to gateway and send prompt
    try:
        asyncio.run(connect_to_gateway(gateway_url, args.prompt))
    except KeyboardInterrupt:
        print("\n👋 Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}")
        logger.error(f"Main traceback: {traceback.format_exc()}")
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
