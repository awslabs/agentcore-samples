"""Demo: invoke API Gateway endpoints through AgentCore Gateway.

Lists tools, then calls pet endpoints (IAM auth) and order endpoint (API Key auth).

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/api-gateway/invoke.py
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
    print("List Pets (IAM Auth)")
    print("=" * 60)
    pets_target = next(
        (
            t["name"]
            for t in all_tools
            if "pet" in t["name"].lower() and "list" in t["name"].lower()
        ),
        None,
    )
    if pets_target:
        print(json.dumps(mcp.call_tool(pets_target, {}), indent=2))
    else:
        print("  No list pets tool found")

    print("\n" + "=" * 60)
    print("Get Pet by ID (IAM Auth)")
    print("=" * 60)
    get_pet_target = next(
        (
            t["name"]
            for t in all_tools
            if "pet" in t["name"].lower() and "id" in t["name"].lower()
        ),
        None,
    )
    if get_pet_target:
        print(json.dumps(mcp.call_tool(get_pet_target, {"petId": "3"}), indent=2))
    else:
        print("  No get pet tool found")

    print("\n" + "=" * 60)
    print("Get Order by ID (API Key Auth)")
    print("=" * 60)
    order_target = next(
        (t["name"] for t in all_tools if "order" in t["name"].lower()),
        None,
    )
    if order_target:
        print(json.dumps(mcp.call_tool(order_target, {"orderId": "2"}), indent=2))
    else:
        print("  No order tool found")


if __name__ == "__main__":
    main()
