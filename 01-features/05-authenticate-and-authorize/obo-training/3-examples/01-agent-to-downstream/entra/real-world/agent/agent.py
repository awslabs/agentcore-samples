"""
Strands agent for the OBO Use Case 1 real-world example.

Runs on AgentCore Runtime. Inbound auth (configured outside this file via
agentcore/agentcore.json) uses Entra ID's OIDC discovery — the Runtime
validates the caller's JWT before the handler runs.

Inside the handler we:
  1. Read the inbound user JWT from context.request_headers["Authorization"].
  2. Perform the OBO exchange via AgentCore Identity.
  3. Call Microsoft Graph /me with the OBO'd token.
  4. Let the LLM compose a natural-language response.

Deploy:
    After running `agentcore create --defaults` and
    `python deploy/02_patch_agentcore_json.py`, copy this file as:
        cp agent/agent.py app/<AGENT_RUNTIME_NAME>/main.py
    then run `agentcore deploy`.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
import requests
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.exceptions import ClientError
from strands import Agent, tool


WORKLOAD_NAME = os.environ.get("WORKLOAD_NAME", "obo-usecase1-entra-realworld")
ACTOR_PROVIDER_NAME = os.environ.get("ACTOR_PROVIDER_NAME", "obo-uc1-entra-realworld-actor")
GRAPH_SCOPE = os.environ.get("GRAPH_SCOPE", "https://graph.microsoft.com/User.Read")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

app = BedrockAgentCoreApp()
log = app.logger
_ac_identity = boto3.client("bedrock-agentcore", region_name=AWS_REGION)


# Per-request state for passing the inbound JWT into the tool. The @tool
# decorator doesn't let us inject the JWT at invocation time, so we set it
# on module state before calling agent().
_current_user_jwt: dict[str, str] = {}


def _obo_exchange(user_token: str) -> str:
    """Swap an inbound user JWT for a Graph-scoped OBO access token.

    Uses AgentCore Identity's two-call pattern:
      1. GetWorkloadAccessTokenForJWT wraps the user JWT.
      2. GetResourceOauth2Token with ON_BEHALF_OF_TOKEN_EXCHANGE performs the
         actual exchange via the configured MicrosoftOauth2 credential provider.
    """
    workload_token = _ac_identity.get_workload_access_token_for_jwt(
        workloadName=WORKLOAD_NAME,
        userToken=user_token,
    )["workloadAccessToken"]

    return _ac_identity.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=ACTOR_PROVIDER_NAME,
        scopes=[GRAPH_SCOPE],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
    )["accessToken"]


@tool
def get_my_profile() -> dict[str, Any]:
    """Look up the caller's Microsoft 365 profile using On-Behalf-Of.

    The tool performs the OBO exchange server-side. Neither the LLM nor the
    tool caller ever sees the resulting Graph token — this is intentional
    to keep the bearer value out of the LLM's context window.
    """
    user_jwt = _current_user_jwt.get("token")
    if not user_jwt:
        return {"error": "No user JWT available on the request context."}

    try:
        graph_token = _obo_exchange(user_jwt)
    except ClientError as e:
        log.error("OBO exchange failed: %s", e)
        return {"error": f"OBO exchange failed: {e}"}

    try:
        r = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {graph_token}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        log.error("Graph call failed: %s", e)
        return {
            "error": f"Graph call failed: {e.response.status_code if e.response else 'unknown'}",
            "detail": e.response.text if e.response else "",
        }


SYSTEM_PROMPT = """
You are an assistant that answers the user's questions about their own
Microsoft 365 profile (name, email, job title, office location, etc.).

To answer:
  1. Call the get_my_profile tool (it takes no arguments — it already knows who the caller is).
  2. Extract the fields relevant to the user's question.
  3. Answer in one or two sentences.

Do not fabricate information not present in the profile.
"""


_agent = None


def _get_or_create_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent(
            model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            system_prompt=SYSTEM_PROMPT,
            tools=[get_my_profile],
        )
    return _agent


@app.entrypoint
async def invoke(payload, context):
    """Runtime entrypoint.

    This is an async generator (it uses `yield`), so we cannot `return <value>` —
    errors are yielded as plain strings instead.
    """
    log.info("Invoking agent for OBO use case 1")

    # Extract the JWT from the incoming request. The Runtime has already
    # validated signature, issuer, and audience — we just need the raw token
    # to pass to AgentCore Identity as the OBO subject.
    headers = context.request_headers or {}
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        yield "ERROR: Missing or malformed Authorization header"
        return
    user_jwt = auth.split(" ", 1)[1]

    _current_user_jwt["token"] = user_jwt
    try:
        prompt = payload.get("prompt", "What is my display name?")
        agent = _get_or_create_agent()
        stream = agent.stream_async(prompt)
        async for event in stream:
            if "data" in event and isinstance(event["data"], str):
                yield event["data"]
    finally:
        _current_user_jwt.pop("token", None)


if __name__ == "__main__":
    app.run()
