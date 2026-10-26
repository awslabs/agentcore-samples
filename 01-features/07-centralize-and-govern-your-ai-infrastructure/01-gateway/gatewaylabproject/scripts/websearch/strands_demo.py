"""Demo: a Strands agent answers a current-events question with Web Search.

Connects a Strands agent to the gateway, which exposes the WebSearch tool. The
agent decides to call WebSearch, grounds its answer in live results, and cites
sources. This shows how managed web search eliminates the training-cutoff limit
without any search-API plumbing.

Requires GATEWAY_URL and COGNITO_STACK_NAME environment variables, plus Bedrock
model access for the model below.

Usage:
    uv run python scripts/websearch/strands_demo.py
    uv run python scripts/websearch/strands_demo.py "What are the latest AWS launches?"
"""

import os
import sys

import boto3
import requests
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


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
    question = (
        sys.argv[1] if len(sys.argv) > 1 else "What are the latest AWS announcements?"
    )
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

    print(f"Gateway URL: {gateway_url}\n")

    client = MCPClient(
        lambda: streamablehttp_client(
            gateway_url, headers={"Authorization": f"Bearer {token}"}
        )
    )

    model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

    with client:
        tools = client.list_tools_sync()
        print(f"Tools loaded: {[t.tool_name for t in tools]}\n")

        agent = Agent(
            model=model,
            tools=tools,
            system_prompt=(
                "You answer using the WebSearch tool for anything time-sensitive. "
                "Cite the source URL for each fact you report."
            ),
        )
        agent(question)


if __name__ == "__main__":
    main()
