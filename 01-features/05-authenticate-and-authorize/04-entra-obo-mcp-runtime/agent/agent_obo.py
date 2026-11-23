"""Use separate transport and user-delegation grants. Only the profile tool sees the Graph grant."""

import os
import re
import uuid
from urllib.parse import quote

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp, BedrockAgentCoreContext
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

app = BedrockAgentCoreApp()
HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Graph-Token"


def destination(arn, region):
    if not re.fullmatch(
        rf"arn:aws:bedrock-agentcore:{re.escape(region)}:[0-9]{{12}}:runtime/[A-Za-z][A-Za-z0-9_]{{0,47}}-[A-Za-z0-9]{{10}}",
        arn,
    ):
        raise ValueError("Expected the configured MCP Runtime ARN in the same Region")
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )


def token_headers(client, workload_token, provider, mcp_app):
    if not workload_token or not provider:
        raise ValueError("Missing workload identity context or configured provider")
    app_id = str(uuid.UUID(mcp_app))
    common = {"workloadIdentityToken": workload_token, "resourceCredentialProviderName": provider}
    transport = client.get_resource_oauth2_token(**common, scopes=[f"api://{app_id}/.default"], oauth2Flow="M2M")[
        "accessToken"
    ]
    delegated = client.get_resource_oauth2_token(
        **common, scopes=["User.Read"], oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE"
    )["accessToken"]
    if not transport or not delegated or transport == delegated:
        raise ValueError("Expected distinct transport and Graph user grants")
    return {"Authorization": "Bearer " + transport, HEADER: delegated}


@app.entrypoint
def handler(payload):
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise ValueError("Expected a nonempty prompt up to 4000 characters")
    region = os.environ["AWS_REGION"]
    url = destination(os.environ["MCP_RUNTIME_ARN"], region)
    with boto3.client("bedrock-agentcore", region_name=region) as identity:
        headers = token_headers(
            identity,
            BedrockAgentCoreContext.get_workload_access_token(),
            os.environ["CREDENTIAL_PROVIDER_NAME"],
            os.environ["ENTRA_MCP_CLIENT_ID"],
        )
    with MCPClient(url=url, headers=headers) as client:
        tools = client.list_tools_sync()
        agent = Agent(
            model=BedrockModel(model_id=os.environ["BEDROCK_MODEL_ID"], region_name=region),
            tools=tools,
            callback_handler=None,
            system_prompt="Use the profile tool to answer the user's question.",
        )
        return str(agent(prompt))


if __name__ == "__main__":
    app.run()
