"""Strands agent on AgentCore Runtime: OBO to the gateway, ID-JAG done for it.

The agent does exactly two identity things, and deliberately no more:

  1. Exchanges the caller's `T_user` for `T_gateway` (scp=tools.access) through
     AgentCore Identity. The Agent app's secret lives in the credential provider, not
     here.
  2. Sends only `Authorization: Bearer T_gateway`; the gateway's interceptor exchanges
     that token at ID-JAG leg 1, so no second credential travels with the request
     so the gateway's interceptor can run the Cross App Access legs.

It never performs the ID-JAG exchange, never sees `T_tool`, and never puts a token in
the prompt. The credential that reaches the todo API exists only inside the gateway's
interceptor.

Why the ID token travels in a **header** rather than the tool arguments: the arguments
are composed by the model, so a credential there would enter the model's context and
could be echoed. MCP sets headers per connection, which suits an ID token because it is
constant for the session.

Environment (written by deploy/05_patch_agentcore_json.py):
  GATEWAY_MCP_URL          the gateway's MCP endpoint
  AGENT_OBO_PROVIDER_NAME  the AgentCore Identity provider for the OBO exchange
  AGENTCORE_AUDIENCE       the audience to request for T_gateway
  SCOPE_TOOLS_ACCESS       the scope to request for T_gateway
  MODEL_ID                 optional Bedrock model override
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.tools.mcp.mcp_client import MCPClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("xaa-agent")

GATEWAY_MCP_URL = os.environ.get("GATEWAY_MCP_URL", "")
OBO_PROVIDER = os.environ.get("AGENT_OBO_PROVIDER_NAME", "xaa-agent-obo-provider")
AUDIENCE = os.environ.get("AGENTCORE_AUDIENCE", "https://xaa-agentcore.example.com")
TOOLS_SCOPE = os.environ.get("SCOPE_TOOLS_ACCESS", "tools.access")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
ID_TOKEN_HEADER = os.environ.get("ID_TOKEN_HEADER", "X-Okta-Id-Token")

app = BedrockAgentCoreApp()
_identity = boto3.client("bedrock-agentcore")

SYSTEM_PROMPT = """You are a todo assistant. Use the provided tools to answer questions
about the user's todo list, and to add or complete items when asked.

Rules:
- Answer only from tool results. Never invent todo items.
- If a tool call fails, say plainly that the request could not be completed and give
  the tool's error message. Do not guess at the cause, and do not retry more than once.
- Never mention tokens, headers, or authorization mechanics. The user does not need
  them and they are not yours to discuss.
"""


def obo_token() -> str:
    """Exchange the caller's token for one scoped to the gateway.

    AgentCore Identity performs the RFC 8693 exchange at Okta as the Agent app, so the
    agent needs no client secret. `subject_token_type` must be set explicitly: the
    service defaults it to `jwt` while Okta requires `access_token`.

    The workload access token is NOT fetched here. Runtime already obtained one -- it
    validated the inbound JWT, took its `iss`/`sub`, looked up the agent's workload
    identity, called GetWorkloadAccessTokenForJWT itself, and delivered the result as a
    request header. The SDK lifts that header into context, so the agent just reads it:
    one less API call, one less IAM action, and no workload identity name to keep in sync.

    The trade-off is real but narrow -- see "Why the agent does not fetch its own workload
    access token" in README.md.
    """
    workload = BedrockAgentCoreContext.get_workload_access_token()
    if not workload:
        raise RuntimeError(
            "No workload access token in context. Runtime supplies one only when the "
            "runtime has inbound auth configured (CUSTOM_JWT) and the caller presented a "
            "valid token -- check deploy/05_patch_agentcore_json.py ran and the runtime "
            "was redeployed. Code running outside Runtime never receives one."
        )
    return _identity.get_resource_oauth2_token(
        workloadIdentityToken=workload,
        resourceCredentialProviderName=OBO_PROVIDER,
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        scopes=[TOOLS_SCOPE],
        audiences=[AUDIENCE],
        customParameters={"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"},
    )["accessToken"]


@contextmanager
def gateway(t_gateway: str, id_token: str = "") -> Iterator[MCPClient]:
    """MCP client carrying what the chain needs.

    Authorization   -> the gateway's CUSTOM_JWT authorizer and Cedar read this, and the
                       interceptor exchanges it at ID-JAG leg 1
    X-Okta-Id-Token -> optional; only needed when the interceptor runs in id_token mode
    """
    if not GATEWAY_MCP_URL:
        raise RuntimeError("GATEWAY_MCP_URL is not set; re-run deploy/05_patch_agentcore_json.py")
    headers = {"Authorization": f"Bearer {t_gateway}"}
    # Only sent when the caller supplied one. The gateway's interceptor exchanges the
    # Authorization bearer by default; the ID token header exists for orgs that have the
    # User access binding but not Machine access (XAA_LEG1_SUBJECT=id_token).
    if id_token:
        headers[ID_TOKEN_HEADER] = id_token
    client = MCPClient(lambda: streamablehttp_client(GATEWAY_MCP_URL, headers=headers))
    with client:
        yield client


def all_tools(client: MCPClient) -> list:
    """Every tool, following tools/list pagination to the end.

    list_tools_sync() returns ONE page plus a pagination token. Reading only the first
    page silently hides the rest, and the model then reports that a tool does not
    exist -- which looks like a model problem rather than a truncated list.
    """
    tools: list = []
    token = None
    while True:
        page = client.list_tools_sync(pagination_token=token)
        tools.extend(page)
        token = getattr(page, "pagination_token", None)
        if not token:
            return tools


@app.entrypoint
async def invoke(payload: dict[str, Any], context: Any):
    # The token itself is no longer read here -- Runtime validated it, derived the user
    # identity from it, and already exchanged it for a workload access token. The check
    # stays because its absence means inbound auth is misconfigured, and failing here is
    # clearer than failing inside the exchange.
    auth = (context.request_headers or {}).get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        yield "ERROR: missing or malformed Authorization header."
        return

    # Optional. Leg 1 normally exchanges T_gateway, which we are about to mint, so a
    # second token does not need to travel with the request at all. A BFF running the
    # interceptor in id_token mode can still send one.
    id_token = payload.get("id_token") or ""

    prompt = payload.get("prompt") or "What is on my todo list?"

    try:
        t_gateway = obo_token()
        log.info("obo exchange ok; calling the gateway")
    except Exception as exc:
        log.exception("obo exchange failed")
        yield f"ERROR: could not obtain a gateway token ({type(exc).__name__}: {exc})."
        return

    with gateway(t_gateway, id_token) as client:
        tools = all_tools(client)
        log.info("gateway tools discovered: %d", len(tools))
        agent = Agent(model=MODEL_ID, system_prompt=SYSTEM_PROMPT, tools=tools)

        # Buffered rather than streamed: a tool error can contain identifiers we would
        # rather inspect than relay verbatim, and once a chunk is yielded it cannot be
        # withdrawn. The BFF concatenates the stream anyway.
        chunks: list[str] = []
        async for event in agent.stream_async(prompt):
            if isinstance(event.get("data"), str):
                chunks.append(event["data"])
        yield "".join(chunks) or "(no answer)"


if __name__ == "__main__":
    app.run()
