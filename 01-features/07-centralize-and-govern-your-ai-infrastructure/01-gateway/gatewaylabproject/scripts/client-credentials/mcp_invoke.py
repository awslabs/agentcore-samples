"""Demo: MCP SDK calling tools via AgentCore Gateway.

Uses the MCP Python SDK directly with streamable HTTP transport.

Requires GATEWAY_URL and COGNITO_STACK_NAME environment variables.

Usage:
    uv run python scripts/client-credentials/mcp_invoke.py
"""

import asyncio
import json
import os
import sys

import boto3
import requests
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_token(token_endpoint, client_id, client_secret, scope):
    response = requests.post(
        token_endpoint,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def main():
    load_env()

    gateway_url = os.environ.get("GATEWAY_URL")
    cognito_stack = os.environ.get("COGNITO_STACK_NAME")
    if not gateway_url:
        print("ERROR: GATEWAY_URL not set. Export it or add to the script .env")
        sys.exit(1)
    if not cognito_stack:
        print("ERROR: COGNITO_STACK_NAME not set. Export it or add to the script .env")
        sys.exit(1)

    region = boto3.Session().region_name
    cfn = boto3.client("cloudformation", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack)["Stacks"][0]["Outputs"]
    }
    gw_client_id = outputs["GatewayClientId"]
    gw_scope = outputs["GatewayScope"]
    gw_client_secret = cognito.describe_user_pool_client(
        UserPoolId=outputs["UserPoolId"], ClientId=gw_client_id
    )["UserPoolClient"]["ClientSecret"]
    token_endpoint = outputs["TokenEndpoint"]

    token = get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    print(f"Gateway URL: {gateway_url}")
    print(f"Token: {token[:20]}...\n")

    async with streamablehttp_client(
        gateway_url, headers={"Authorization": f"Bearer {token}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=" * 60)
            print("tools/list")
            print("=" * 60)
            tools_result = await session.list_tools()
            for tool in tools_result.tools:
                print(f"  {tool.name}: {tool.description or ''}")

            print("\n" + "=" * 60)
            print("tools/call — getOrder")
            print("=" * 60)
            get_order = next(
                (t.name for t in tools_result.tools if "getOrder" in t.name),
                None,
            )
            if get_order:
                result = await session.call_tool(get_order, {"orderId": "123"})
                print(
                    json.dumps(
                        {"content": [c.model_dump() for c in result.content]}, indent=2
                    )
                )
            else:
                print("  getOrder tool not found")


if __name__ == "__main__":
    asyncio.run(main())
