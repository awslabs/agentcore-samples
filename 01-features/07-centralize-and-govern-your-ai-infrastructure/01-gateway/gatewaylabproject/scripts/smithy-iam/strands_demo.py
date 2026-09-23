"""Demo: Strands Agent calling Smithy S3 MCP tools via AgentCore Gateway.

Connects a Strands Agent to the gateway and invokes S3 tools via natural language.

Requires GATEWAY_URL and COGNITO_STACK_NAME environment variables.

Usage:
    uv run python scripts/smithy-iam/strands_demo.py
"""

import os
import sys

import boto3
import requests
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client


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

    token = get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    print(f"Gateway URL: {gateway_url}")
    print(f"Token: {token[:20]}...\n")

    client = MCPClient(
        lambda: streamablehttp_client(
            gateway_url, headers={"Authorization": f"Bearer {token}"}
        )
    )

    model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

    with client:
        tools = client.list_tools_sync()
        print(f"Tools loaded: {[t.tool_name for t in tools]}\n")

        agent = Agent(model=model, tools=tools)
        agent("List all the S3 buckets in my account")


if __name__ == "__main__":
    main()
