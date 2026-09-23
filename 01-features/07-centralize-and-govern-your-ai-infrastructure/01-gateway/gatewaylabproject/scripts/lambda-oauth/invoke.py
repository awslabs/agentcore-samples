"""Demo: Invoke Lambda MCP tools through AgentCore Gateway.

Lists tools and calls get_order_tool and update_order_tool via the gateway.

Requires GATEWAY_URL and COGNITO_STACK_NAME environment variables.

Usage:
    uv run python scripts/lambda-oauth/invoke.py
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

    def token_fn():
        return get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    mcp = GatewayMCPClient(gateway_url, token_fn)

    print(f"Gateway URL: {gateway_url}\n")

    print("=" * 60)
    print("tools/list")
    print("=" * 60)
    all_tools = mcp.list_all_tools()
    for t in all_tools:
        print(f"  {t['name']}: {t.get('description', '')}")

    print("\n" + "=" * 60)
    print("Get Order (get_order_tool)")
    print("=" * 60)
    get_order = next(
        (t["name"] for t in all_tools if "get_order" in t["name"]),
        None,
    )
    if get_order:
        print(json.dumps(mcp.call_tool(get_order, {"orderId": "123"}), indent=2))
    else:
        print("  Tool not found")

    print("\n" + "=" * 60)
    print("Update Order (update_order_tool)")
    print("=" * 60)
    update_order = next(
        (t["name"] for t in all_tools if "update_order" in t["name"]),
        None,
    )
    if update_order:
        print(json.dumps(mcp.call_tool(update_order, {"orderId": "123"}), indent=2))
    else:
        print("  Tool not found")


if __name__ == "__main__":
    main()
