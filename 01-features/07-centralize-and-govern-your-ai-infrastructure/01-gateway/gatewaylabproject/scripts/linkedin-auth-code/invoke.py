"""Demo: Invoke LinkedIn tools through AgentCore Gateway.

Lists tools and calls getUserInfo. Handles URL-mode elicitation.

Requires GATEWAY_URL, COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/linkedin-auth-code/invoke.py
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

    gateway_url = os.environ.get("GATEWAY_URL")
    cognito_stack = os.environ.get("COGNITO_STACK_NAME", "agentcore-gateway-lab")
    if not gateway_url:
        print("ERROR: GATEWAY_URL not set. Export it or add to the script .env")
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

    access_token = get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    mcp = GatewayMCPClient(
        gateway_url, lambda: access_token, protocol_version="2025-11-25"
    )

    print(f"Gateway URL: {gateway_url}\n")

    print("=" * 60)
    print("tools/list")
    print("=" * 60)
    raw = mcp.list_tools()
    if "error" in raw:
        print(f"  ERROR: {json.dumps(raw['error'], indent=2)}")
        return
    all_tools = mcp.list_all_tools()
    for t in all_tools:
        print(f"  {t['name']}")
    print(f"\n  ({len(all_tools)} tools)")

    print("\n" + "=" * 60)
    print("tools/call — getUserInfo")
    print("=" * 60)
    tool_name = next(
        (t["name"] for t in all_tools if "getUserInfo" in t["name"]),
        None,
    )
    if not tool_name:
        print("  getUserInfo tool not found")
        return

    result = mcp.call_tool(tool_name, {})

    error = result.get("error", {})
    if error.get("code") == -32042:
        elicitations = error.get("data", {}).get("elicitations", [])
        if elicitations and elicitations[0].get("mode") == "url":
            auth_url = elicitations[0]["url"]
            print("\n  LinkedIn authorization required.")
            print(f"  Authorization URL: {auth_url}")
            print("\n  Start the callback server in another terminal:")
            print(
                f'  uv run python scripts/linkedin-auth-code/callback_server.py --user-token "{access_token}" --auth-url "{auth_url}"'
            )
            print("\n  After authorizing, run this script again.")
            return

    print(json.dumps(result, indent=2)[:2000])


if __name__ == "__main__":
    main()
