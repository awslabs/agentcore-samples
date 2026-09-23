"""Demo: invoke tools, prompts, and resources through AgentCore Gateway.

Reads gateway URL and Cognito credentials from the .env file
written by deploy_gateway.py.

Usage:
    uv run python scripts/invoke.py
"""

import json
import os
import sys

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_mcp_client import GatewayMCPClient

PROJECT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env[key] = value
    return env


def get_token(
    token_endpoint: str, client_id: str, client_secret: str, scope: str
) -> str:
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
    env = load_env()
    gateway_url = os.environ.get("GATEWAY_URL") or env.get("GATEWAY_URL")
    cognito_stack_name = os.environ.get("COGNITO_STACK_NAME") or env.get(
        "COGNITO_STACK_NAME", "agentcore-gateway-lab"
    )
    if not gateway_url:
        print("ERROR: GATEWAY_URL not set. Export it or add to the script .env")
        sys.exit(1)

    cfn = boto3.client("cloudformation", region_name=boto3.Session().region_name)
    cognito = boto3.client("cognito-idp", region_name=boto3.Session().region_name)

    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack_name)["Stacks"][0][
            "Outputs"
        ]
    }
    token_endpoint = outputs["TokenEndpoint"]
    gw_client_id = outputs["GatewayClientId"]
    gw_scope = outputs["GatewayScope"]
    gw_client_secret = cognito.describe_user_pool_client(
        UserPoolId=outputs["UserPoolId"], ClientId=gw_client_id
    )["UserPoolClient"]["ClientSecret"]

    def _get_token() -> str:
        return get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    mcp = GatewayMCPClient(gateway_url, _get_token)

    print(f"Gateway URL: {gateway_url}\n")

    # Tools
    print("=" * 60)
    print("tools/list")
    print("=" * 60)
    print(json.dumps(mcp.list_tools(), indent=2))

    print("\n" + "=" * 60)
    print("tools/call — getOrder")
    print("=" * 60)
    print(
        json.dumps(
            mcp.call_tool("client-credentials-mcp-server-target___getOrder", {}),
            indent=2,
        )
    )

    # Prompts
    print("\n" + "=" * 60)
    print("prompts/list")
    print("=" * 60)
    print(json.dumps(mcp.list_prompts(), indent=2))

    print("\n" + "=" * 60)
    print("prompts/get — order_summary_prompt")
    print("=" * 60)
    print(
        json.dumps(
            mcp.get_prompt(
                "client-credentials-mcp-server-target___order_summary_prompt",
                {"orderId": "123"},
            ),
            indent=2,
        )
    )

    # Resources
    print("\n" + "=" * 60)
    print("resources/list")
    print("=" * 60)
    print(json.dumps(mcp.list_resources(), indent=2))

    print("\n" + "=" * 60)
    print("resources/read — orders://catalog")
    print("=" * 60)
    print(json.dumps(mcp.read_resource("orders://catalog"), indent=2))

    print("\n" + "=" * 60)
    print("resources/templates/list")
    print("=" * 60)
    print(json.dumps(mcp.list_resource_templates(), indent=2))

    print("\n" + "=" * 60)
    print("resources/read — orders://123/details")
    print("=" * 60)
    print(json.dumps(mcp.read_resource("orders://123/details"), indent=2))


if __name__ == "__main__":
    main()
