"""Demo: Strands Agent with AgentCore Gateway semantic search.

Uses the built-in x_amz_bedrock_agentcore_search tool to find relevant tools,
then creates a Strands Agent with only those tools (instead of all 300+).

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/semantic-search/strands_demo.py
"""

import os
import sys

import boto3
import requests
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool as MCPTool
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPAgentTool, MCPClient


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

    jwt_token = get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    print(f"Gateway URL: {gateway_url}")
    print("Token obtained.\n")

    # --- Step 1: Use semantic search to find relevant tools ---
    print("=" * 60)
    print("Step 1: Semantic search for 'tools for adding numbers'")
    print("=" * 60)

    search_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "x_amz_bedrock_agentcore_search",
            "arguments": {"query": "tools for adding numbers"},
        },
    }
    response = requests.post(  # nosec B113
        gateway_url,
        json=search_request,
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-11-25",
        },
    )
    result = response.json()
    tools_found = result.get("result", {}).get("structuredContent", {}).get("tools", [])
    print(f"  Found {len(tools_found)} relevant tools:")
    for t in tools_found[:5]:
        print(f"    - {t['name']}: {t.get('description', '')[:60]}")

    if not tools_found:
        print("\n  No tools found. Ensure targets are deployed and indexed.")
        return

    # --- Step 2: Create Strands Agent with only the relevant tools ---
    print("\n" + "=" * 60)
    print("Step 2: Create Strands Agent with relevant tools only")
    print("=" * 60)

    client = MCPClient(
        lambda: streamablehttp_client(
            gateway_url, headers={"Authorization": f"Bearer {jwt_token}"}
        )
    )
    model = BedrockModel(
        model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0", temperature=0.7
    )

    with client:
        strands_tools = []
        for tool in tools_found[:3]:
            mcp_tool = MCPTool(
                name=tool["name"],
                description=tool.get("description", ""),
                inputSchema=tool["inputSchema"],
            )
            strands_tools.append(MCPAgentTool(mcp_tool, client))

        print(f"  Agent tools: {[t.tool_name for t in strands_tools]}")

        agent = Agent(model=model, tools=strands_tools)

        # --- Step 3: Run the agent ---
        print("\n" + "=" * 60)
        print("Step 3: Agent query — 'add 100 plus 50'")
        print("=" * 60)

        result = agent("add 100 plus 50")
        print(f"\n  Agent response: {result.message['content'][0]['text']}")


if __name__ == "__main__":
    main()
