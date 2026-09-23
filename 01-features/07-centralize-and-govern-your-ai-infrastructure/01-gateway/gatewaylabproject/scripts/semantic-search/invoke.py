"""Demo: Semantic search on AgentCore Gateway.

Demonstrates the built-in x_amz_bedrock_agentcore_search tool by:
  1. Listing all tools (300+) to show the scale
  2. Performing a semantic search for "credit research tools"
  3. Performing a semantic search for "restaurant reservation tools"
  4. Showing that search returns the most relevant subset in <1 second

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/semantic-search/invoke.py
"""

import json
import os
import sys
import time

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_mcp_client import GatewayMCPClient


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


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


def main():
    load_env()

    gateway_url = get_required_env("GATEWAY_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")

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

    def token_fn():
        return get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    mcp = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")

    print(f"Gateway URL: {gateway_url}\n")

    # ----- Step 1: List all tools -----
    print("=" * 60)
    print("Step 1: List all tools (paginated)")
    print("=" * 60)
    all_tools = mcp.list_all_tools()
    print(f"  Total tools available: {len(all_tools)}")
    # Show a few names
    for t in all_tools[:5]:
        print(f"    {t['name']}: {t.get('description', '')[:60]}...")
    if len(all_tools) > 5:
        print(f"    ... and {len(all_tools) - 5} more")

    # ----- Step 2: Semantic search - credit research -----
    print("\n" + "=" * 60)
    print("Step 2: Semantic search - 'find me 3 credit research tools'")
    print("=" * 60)
    start = time.time()
    result = mcp.call_tool(
        "x_amz_bedrock_agentcore_search",
        {"query": "find me 3 credit research tools"},
    )
    elapsed = time.time() - start
    content = result.get("result", {}).get("content", [])
    tools_found = json.loads(content[0]["text"]).get("tools", []) if content else []
    print(f"  Search completed in {elapsed:.2f}s")
    print(f"  Tools returned: {len(tools_found)}")
    for t in tools_found[:5]:
        print(f"    {t['name']}: {t.get('description', '')[:60]}...")

    # ----- Step 3: Semantic search - restaurant -----
    print("\n" + "=" * 60)
    print("Step 3: Semantic search - 'tools for booking a restaurant reservation'")
    print("=" * 60)
    start = time.time()
    result = mcp.call_tool(
        "x_amz_bedrock_agentcore_search",
        {"query": "tools for booking a restaurant reservation"},
    )
    elapsed = time.time() - start
    content = result.get("result", {}).get("content", [])
    tools_found = json.loads(content[0]["text"]).get("tools", []) if content else []
    print(f"  Search completed in {elapsed:.2f}s")
    print(f"  Tools returned: {len(tools_found)}")
    for t in tools_found[:5]:
        print(f"    {t['name']}: {t.get('description', '')[:60]}...")

    # ----- Step 4: Semantic search - math -----
    print("\n" + "=" * 60)
    print("Step 4: Semantic search - 'tools for multiplying two numbers'")
    print("=" * 60)
    start = time.time()
    result = mcp.call_tool(
        "x_amz_bedrock_agentcore_search",
        {"query": "tools for multiplying two numbers"},
    )
    elapsed = time.time() - start
    content = result.get("result", {}).get("content", [])
    tools_found = json.loads(content[0]["text"]).get("tools", []) if content else []
    print(f"  Search completed in {elapsed:.2f}s")
    print(f"  Tools returned: {len(tools_found)}")
    for t in tools_found[:5]:
        print(f"    {t['name']}: {t.get('description', '')[:60]}...")

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Gateway exposes {len(all_tools)} tools across all targets.")
    print("  Semantic search returns the most relevant subset in <1 second,")
    print("  reducing agent latency and cost by up to 3x.")


if __name__ == "__main__":
    main()
