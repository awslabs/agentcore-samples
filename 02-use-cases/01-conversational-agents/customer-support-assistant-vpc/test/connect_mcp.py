#!/usr/bin/env python3

import argparse
import asyncio
import logging
import sys
import traceback
import urllib.parse
from bedrock_agentcore.identity.auth import requires_access_token
from datetime import timedelta
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from utils import get_ssm_parameter

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def create_mcp_client(provider_name, agent_arn):
    """Create MCP client with given parameters"""

    # Extract runtime_id, region, and account_id from ARN
    # ARN format: arn:aws:bedrock-agentcore:region:account-id:runtime/runtime-id
    runtime_id = agent_arn.split("/")[-1]
    arn_parts = agent_arn.split(":")
    region = arn_parts[3]
    account_id = arn_parts[4]

    print(f"📋 AWS Account ID: {account_id}")
    print(f"🌍 AWS Region: {region}")
    print(f"🤖 MCP Runtime ID: {runtime_id}")

    @requires_access_token(
        provider_name=provider_name,
        scopes=[],
        auth_flow="M2M",
        into="bearer_token",
        force_authentication=True,
    )
    async def connect(bearer_token):
        print(f"Bearer token received: {bearer_token}")

        print(agent_arn)
        escaped_arn = urllib.parse.quote(agent_arn, safe="")
        mcp_url = (
            f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations?qualifier=DEFAULT"
        )

        headers = {
            "authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }

        print(f"🔗 Connecting to: {mcp_url}")
        logger.info(f"Agent ARN: {agent_arn}")
        logger.info(f"Headers: {dict(headers)}")

        try:
            logger.info("Creating streamable HTTP client...")
            async with streamablehttp_client(
                mcp_url,
                headers,
                timeout=timedelta(seconds=120),
                terminate_on_close=False,
            ) as (read_stream, write_stream, _):
                logger.info("HTTP client created successfully")
                logger.info("Creating MCP client session...")

                try:
                    async with ClientSession(read_stream, write_stream) as session:
                        print("🔄 Initializing MCP session...")
                        logger.info("Calling session.initialize()...")
                        await session.initialize()
                        logger.info("Session initialized successfully")
                        print("✅ MCP session initialized")

                        # List available tools
                        print("\n🔄 Listing available tools...")
                        logger.info("Calling session.list_tools()...")
                        tool_result = await session.list_tools()
                        logger.info(f"Got {len(tool_result.tools)} tools")

                        print("\n📋 Available MCP Tools:")
                        print("=" * 50)
                        for tool in tool_result.tools:
                            print(f"🔧 {tool.name}")
                            print(f"   Description: {tool.description}")
                            if hasattr(tool, "inputSchema") and tool.inputSchema:
                                properties = tool.inputSchema.get("properties", {})
                                if properties:
                                    print(f"   Parameters: {list(properties.keys())}")
                            print()

                        print(f"✅ Found {len(tool_result.tools)} tools available.")

                        # Test some tools
                        print("\n🧪 Testing MCP Tools:")
                        print("=" * 50)

                        test_cases = [
                            ("get_reviews", {"review_id": "1"}),
                            ("get_products", {"product_id": 1}),
                        ]

                        for tool_name, args in test_cases:
                            try:
                                print(f"\n➕ Testing {tool_name}({args})...")
                                logger.info(f"Calling tool {tool_name} with args {args}")
                                result = await session.call_tool(name=tool_name, arguments=args)
                                logger.info(f"Tool {tool_name} returned: {result}")
                                if result.content:
                                    print(f"   Result: {result.content[0].text}")
                                else:
                                    print("   No content returned")
                            except Exception as e:
                                logger.error(f"Error calling tool {tool_name}: {e}")
                                logger.error(f"Traceback: {traceback.format_exc()}")
                                print(f"   Error: {e}")

                except Exception as session_e:
                    logger.error(f"Error in MCP session: {session_e}")
                    logger.error(f"Session traceback: {traceback.format_exc()}")
                    raise session_e

        except Exception as e:
            logger.error(f"Error in streamable HTTP client: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            print(f"❌ Error connecting to MCP server: {e}")

            # Print any nested exception details
            if hasattr(e, "__cause__") and e.__cause__:
                logger.error(f"Caused by: {e.__cause__}")
                logger.error(
                    f"Cause traceback: {traceback.format_exception(type(e.__cause__), e.__cause__, e.__cause__.__traceback__)}"
                )

            if hasattr(e, "__context__") and e.__context__:
                logger.error(f"Context: {e.__context__}")

            sys.exit(1)

    return connect


def main():
    parser = argparse.ArgumentParser(description="MCP DynamoDB CLI Tool")
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

    print("🚀 MCP DynamoDB CLI Tool")
    print("=" * 30)

    # Get MCP Runtime ARN and Provider Name from SSM Parameter Store
    agent_arn = get_ssm_parameter("/app/customersupportvpc/mcp/mcp_runtime_arn")
    provider_name = get_ssm_parameter("/app/customersupportvpc/mcp/mcp_provider_name")

    print(f"🤖 MCP Runtime ARN: {agent_arn}")
    print(f"🔐 OAuth2 Provider: {provider_name}")

    # Create and run the MCP client
    try:
        client = create_mcp_client(provider_name, agent_arn)
        asyncio.run(client())
    except KeyboardInterrupt:
        print("\n👋 Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}")
        logger.error(f"Main traceback: {traceback.format_exc()}")
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
