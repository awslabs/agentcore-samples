"""Demo: call the Web Search Tool through the gateway over MCP.

Lists the gateway tools, finds the WebSearch tool, and calls it with a natural
language query. Prints the structured results (text snippet, url, title,
publishedDate) the tool returns.

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/websearch/invoke.py
    uv run python scripts/websearch/invoke.py "what shipped in python 3.13"
"""

import json
import os
import sys

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
    query = sys.argv[1] if len(sys.argv) > 1 else "What was announced at AWS this week?"
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

    # --- List tools ---
    print("=" * 60)
    print("tools/list")
    print("=" * 60)
    all_tools = mcp.list_all_tools()
    for t in all_tools:
        print(f"  {t['name']}: {t.get('description', '')}")

    # The tool name is prefixed by the target name (target___WebSearch).
    websearch_tool = next(
        (t["name"] for t in all_tools if t["name"].lower().endswith("websearch")),
        None,
    )
    if not websearch_tool:
        print("\n  WebSearch tool not found. Run deploy.py first.")
        return

    # --- Call WebSearch ---
    print("\n" + "=" * 60)
    print(f"tools/call - {websearch_tool}")
    print(f"  query: {query!r}")
    print("=" * 60)
    result = mcp.call_tool(websearch_tool, {"query": query, "maxResults": 5})

    # call_tool returns the JSON-RPC envelope; the MCP content is nested under
    # "result". Unwrap it (fall back to top level for older shapes).
    tool_result = result.get("result", result)
    if tool_result.get("error") or result.get("error"):
        print(f"\n  Tool error: {result.get('error') or tool_result.get('error')}")
        return

    # The tool returns MCP content; the first text block is JSON with results.
    for block in tool_result.get("content", []):
        if block.get("type") == "text":
            try:
                payload = json.loads(block["text"])
            except (json.JSONDecodeError, KeyError):
                print(block.get("text", ""))
                continue
            for r in payload.get("results", []):
                print(f"\n  {r.get('title', '(no title)')}")
                print(f"    {r.get('url', '')}  ({r.get('publishedDate', 'n/a')})")
                print(f"    {r.get('text', '')[:200]}")


if __name__ == "__main__":
    main()
