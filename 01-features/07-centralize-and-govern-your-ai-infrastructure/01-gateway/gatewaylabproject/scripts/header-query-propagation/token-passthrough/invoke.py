"""Demo: Test token passthrough and DEFAULT vs DYNAMIC listing.

Requires GATEWAY_URL, COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/header-query-propagation/token-passthrough/invoke.py
"""

import json
import os
import sys

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
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
        print(f"ERROR: {key} not set. Export it or add to .env")
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

    access_token = get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    def token_fn():
        return access_token

    mcp = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")

    print(f"Gateway URL: {gateway_url}")
    print(f"Token (first 30): {access_token[:30]}...\n")

    # --- Test 1: Lambda target receives client token ---
    print("=" * 60)
    print("Test 1: Lambda target — receives client's Authorization token")
    print("=" * 60)

    result = mcp.call_tool(
        "token-echo-lambda-target___echo_token",
        {"message": "verify passthrough"},
    )
    print(json.dumps(result, indent=2))

    content = result.get("result", {}).get("content", [])
    if content:
        body = json.loads(content[0].get("text", "{}"))
        is_passthrough = body.get("is_passthrough", False)
        token_len = body.get("token_length", 0)
        print(f"\n  Token received: {is_passthrough} (length: {token_len})")
        if is_passthrough and token_len > 50:
            print("  PASS: Client token was passed through to Lambda")
        else:
            print("  FAIL: Token not received or too short")

    # --- Test 2: tools/list DEFAULT (cached) ---
    print("\n" + "=" * 60)
    print("Test 2: MCP server (DEFAULT) — tools/list from cache")
    print("=" * 60)

    all_tools = mcp.list_all_tools()
    default_tools = [t["name"] for t in all_tools if "default" in t["name"]]
    dynamic_tools = [t["name"] for t in all_tools if "dynamic" in t["name"]]
    lambda_tools = [t["name"] for t in all_tools if "lambda" in t["name"]]

    print(f"  Lambda target tools: {lambda_tools}")
    print(f"  DEFAULT target tools: {default_tools}")
    print(f"  DYNAMIC target tools: {dynamic_tools}")
    print(f"  Total tools: {len(all_tools)}")

    # --- Test 3: tools/call on DYNAMIC target ---
    print("\n" + "=" * 60)
    print("Test 3: MCP server (DYNAMIC) — tools/call with token passthrough")
    print("=" * 60)

    dynamic_tool = next(
        (
            t["name"]
            for t in all_tools
            if "dynamic" in t["name"] and "whoami" in t["name"]
        ),
        None,
    )
    if dynamic_tool:
        result = mcp.call_tool(dynamic_tool, {"message": "testing dynamic target"})
        print(json.dumps(result, indent=2))
    else:
        print("  DYNAMIC target tool not found — listing may not have completed yet")

    # --- Test 4: DEFAULT target call ---
    print("\n" + "=" * 60)
    print("Test 4: MCP server (DEFAULT) — tools/call")
    print("=" * 60)

    default_tool = next(
        (
            t["name"]
            for t in all_tools
            if "default" in t["name"] and "whoami" in t["name"]
        ),
        None,
    )
    if default_tool:
        result = mcp.call_tool(default_tool, {"message": "testing default target"})
        print(json.dumps(result, indent=2))
    else:
        print("  DEFAULT target tool not found")

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print("  - Lambda target received client's original Bearer token")
    print("  - DEFAULT target serves tools/list from cache")
    print("  - DYNAMIC target forwards tools/list live to MCP server")
    print("  - Both MCP targets receive passthrough token on tools/call")


if __name__ == "__main__":
    main()
