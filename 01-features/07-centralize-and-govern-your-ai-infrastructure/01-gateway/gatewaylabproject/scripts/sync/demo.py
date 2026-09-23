"""Demo: MCP target synchronization through AgentCore Gateway.

Subcommands:
    list-tools         List tools through the gateway
    explicit-sync      Call SynchronizeGatewayTargets, then list tools
    list-all           List all tools across both DEFAULT and DYNAMIC targets

Requires GATEWAY_URL and COGNITO_STACK_NAME environment variables,
or a .env file written by the deployment steps.

Usage:
    uv run python scripts/sync/demo.py list-tools
    uv run python scripts/sync/demo.py explicit-sync
    uv run python scripts/sync/demo.py list-all
"""

import argparse
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


def make_client(gateway_url, token_fn):
    import uuid

    return GatewayMCPClient(gateway_url, token_fn, session_id=str(uuid.uuid4()))


def cmd_list_tools(gateway_url, token_fn):
    mcp = make_client(gateway_url, token_fn)
    print(json.dumps(mcp.list_tools(), indent=2))


def cmd_explicit_sync(gateway_url, token_fn):
    gateway_id = get_required_env("GATEWAY_ID")
    target_id = get_required_env("TARGET_ID")
    region = boto3.Session().region_name

    print("--- tools/list BEFORE sync (should be stale) ---")
    mcp = make_client(gateway_url, token_fn)
    print(json.dumps(mcp.list_tools(), indent=2))

    print("\n--- Calling SynchronizeGatewayTargets ---")
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    response = client.synchronize_gateway_targets(
        gatewayIdentifier=gateway_id,
        targetIdList=[target_id],
    )
    print(json.dumps(str(response), indent=2))

    print("\nWaiting 10 seconds for sync to complete...")
    time.sleep(10)

    print("\n--- tools/list AFTER sync (should include new tool) ---")
    mcp = make_client(gateway_url, token_fn)
    print(json.dumps(mcp.list_tools(), indent=2))


def cmd_list_all(gateway_url, token_fn):
    mcp = make_client(gateway_url, token_fn)
    all_tools = mcp.list_all_tools()
    print(f"{len(all_tools)} tools across all targets:")
    for t in all_tools:
        print(f"  - {t['name']}")


def main():
    load_env()

    parser = argparse.ArgumentParser(description="MCP target synchronization demos")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-tools", help="List tools through the gateway")
    subparsers.add_parser(
        "explicit-sync", help="SynchronizeGatewayTargets then list tools"
    )
    subparsers.add_parser(
        "list-all", help="List all tools across DEFAULT and DYNAMIC targets"
    )

    args = parser.parse_args()

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

    commands = {
        "list-tools": cmd_list_tools,
        "explicit-sync": cmd_explicit_sync,
        "list-all": cmd_list_all,
    }
    commands[args.command](gateway_url, token_fn)


if __name__ == "__main__":
    main()
