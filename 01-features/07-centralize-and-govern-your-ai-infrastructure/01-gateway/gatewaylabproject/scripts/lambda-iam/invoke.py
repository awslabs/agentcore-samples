"""Demo: Invoke Lambda MCP tools through AgentCore Gateway using IAM SigV4 auth.

Lists tools and calls get_order_tool via the gateway using AWS SigV4 signing
for inbound authentication (no OAuth token needed).

Requires GATEWAY_URL environment variable.

Usage:
    uv run python scripts/lambda-iam/invoke.py
"""

import asyncio
import json
import os
import sys

import boto3
from botocore.credentials import Credentials
from mcp.client.session import ClientSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from streamable_http_sigv4 import streamablehttp_client_with_sigv4


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


async def main():
    load_env()

    gateway_url = os.environ.get("GATEWAY_URL")
    if not gateway_url:
        print("ERROR: GATEWAY_URL not set. Export it or add to the script .env")
        sys.exit(1)

    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()
    region = session.region_name

    sigv4_credentials = Credentials(
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
        token=credentials.token,
    )

    print(f"Gateway URL: {gateway_url}")
    print(f"Region: {region}")
    print("Service: bedrock-agentcore\n")

    async with streamablehttp_client_with_sigv4(
        url=gateway_url,
        credentials=sigv4_credentials,
        service="bedrock-agentcore",
        region=region,
    ) as (read, write, _):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()

            print("=" * 60)
            print("tools/list")
            print("=" * 60)
            tools_result = await mcp_session.list_tools()
            for tool in tools_result.tools:
                print(f"  {tool.name}: {tool.description or ''}")

            print("\n" + "=" * 60)
            print("Get Order (get_order_tool)")
            print("=" * 60)
            get_order = next(
                (t.name for t in tools_result.tools if "get_order" in t.name),
                None,
            )
            if get_order:
                result = await mcp_session.call_tool(get_order, {"orderId": "123"})
                print(
                    json.dumps(
                        {"content": [c.model_dump() for c in result.content]}, indent=2
                    )
                )
            else:
                print("  Tool not found")

            print("\n" + "=" * 60)
            print("Update Order (update_order_tool)")
            print("=" * 60)
            update_order = next(
                (t.name for t in tools_result.tools if "update_order" in t.name),
                None,
            )
            if update_order:
                result = await mcp_session.call_tool(update_order, {"orderId": "123"})
                print(
                    json.dumps(
                        {"content": [c.model_dump() for c in result.content]}, indent=2
                    )
                )
            else:
                print("  Tool not found")


if __name__ == "__main__":
    asyncio.run(main())
