"""Agent that performs Entra ID OBO token exchange to call Microsoft Graph via an MCP tool."""

import os
import boto3
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client
from bedrock_agentcore.runtime import BedrockAgentCoreApp, BedrockAgentCoreContext
from bedrock_agentcore.identity.auth import requires_access_token

app = BedrockAgentCoreApp()

MCP_URL = os.environ["MCP_URL"]
MCP_CLIENT_ID = os.environ["ENTRA_MCP_CLIENT_ID"]
CREDENTIAL_PROVIDER_NAME = os.environ.get("CREDENTIAL_PROVIDER_NAME", "entra-agent-provider")
REGION = os.environ.get("AWS_REGION", "us-west-2")

# Must match the header name allowlisted on the MCP runtime (Step 4).
# AgentCore Runtime only forwards headers whose names start with
# 'X-Amzn-Bedrock-AgentCore-Runtime-Custom-' AND are in the runtime's requestHeaderAllowlist.
GRAPH_TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Graph-Token"

# Graph scopes we request in the OBO exchange. These must be delegated permissions
# granted to the Agent app in Entra (and admin-consented).
GRAPH_SCOPES = ["User.Read"]

bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    temperature=0.1,
)


def exchange_for_graph_token() -> str:
    """Exchange the inbound user JWT for a Graph-scoped OBO delegation token.

    Uses the workload access token populated on BedrockAgentCoreContext by the runtime,
    then calls GetResourceOauth2Token with oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE.
    The returned token has aud=Microsoft Graph and carries sub=user. Microsoft records the
    acting middle-tier service in the xms_act.sub claim.

    See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/on-behalf-of-token-exchange.html
    """
    workload_token = BedrockAgentCoreContext.get_workload_access_token()
    if not workload_token:
        raise RuntimeError("No workload access token on context; did Runtime deliver it?")

    client = boto3.client("bedrock-agentcore", region_name=REGION)
    response = client.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=CREDENTIAL_PROVIDER_NAME,
        scopes=GRAPH_SCOPES,
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
    )
    return response["accessToken"]


# The @requires_access_token decorator fetches an M2M token (client_credentials grant)
# for the MCP transport. Same credential provider is used for both flows.
@requires_access_token(
    provider_name="entra-agent-provider",
    scopes=[f"api://{MCP_CLIENT_ID}/.default"],
    auth_flow="M2M",
    into="m2m_token",
)
def invoke_agent(prompt: str, *, m2m_token: str) -> str:
    """invoke_agent with two tokens:
    - M2M token in the Authorization header (identifies the agent, authorizes transport).
    - Graph OBO token in a custom request header (used by MCP tools as the Bearer credential
      when calling Microsoft Graph).

    The LLM never sees either token. Tool signatures expose no auth parameters.
    """
    # 1. OBO exchange: user JWT -> Graph-scoped delegation token.
    graph_token = exchange_for_graph_token()

    # 2. Both tokens travel as HTTP headers. Neither reaches the LLM context.
    headers = {
        "authorization": f"Bearer {m2m_token}",
        GRAPH_TOKEN_HEADER: graph_token,
    }
    mcp_client = MCPClient(lambda: streamablehttp_client(MCP_URL, headers))

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        agent = Agent(
            model=bedrock_model,
            tools=tools,
            system_prompt="You are a helpful assistant. Use the available tools to answer user questions.",
        )
        return str(agent(prompt))


@app.entrypoint
def handler(payload, context):
    prompt = payload.get("prompt", "hello")
    return invoke_agent(prompt)


if __name__ == "__main__":
    app.run()
