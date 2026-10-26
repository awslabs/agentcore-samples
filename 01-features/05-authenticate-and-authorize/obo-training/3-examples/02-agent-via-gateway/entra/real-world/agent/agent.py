"""
Strands agent for the OBO Use Case 2 real-world example (Entra).

Runs on AgentCore Runtime. Inbound auth (configured in agentcore.json by
deploy/03_patch_agentcore_json.py) uses Entra OIDC — the Runtime validates
the inbound JWT before this handler runs.

Inside the handler we:
  1. Read the inbound user JWT from context.request_headers["Authorization"].
  2. Perform OBO #1 via AgentCore Identity:
       T_user (aud=AgentApp) → T_gateway (aud=GatewayApp).
  3. Open an MCP client connection to AgentCore Gateway, presenting
     T_gateway as the Bearer credential.
  4. Hand the MCP client + tools to a Strands LLM Agent. The LLM picks the
     `getMyProfile` tool and calls it via MCP.
  5. The Gateway transparently performs OBO #2 (T_gateway → T_graph) and
     calls Microsoft Graph. The agent never sees T_graph.
  6. Stream the LLM's natural-language response back to the caller.

What this agent does NOT do:
  - Talk to Microsoft Graph directly. There's no `requests.get(graph)` here.
  - Mint, store, or forward Graph-scoped tokens. That's the Gateway's job.

Deploy:
    After running `agentcore create --defaults` and
    `python ../deploy/03_patch_agentcore_json.py`, copy this file as:
        cp agent/agent.py app/$AGENT_RUNTIME_NAME/main.py
    then run `agentcore deploy -y -v`.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.exceptions import ClientError
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.tools.mcp.mcp_client import MCPClient


AGENT_WORKLOAD_NAME = os.environ.get("AGENT_WORKLOAD_NAME", "obo-uc2-entra-agent")
AGENT_OBO_PROVIDER_NAME = os.environ.get("AGENT_OBO_PROVIDER_NAME", "obo-uc2-entra-agent-actor")
GATEWAY_SCOPE = os.environ.get("GATEWAY_SCOPE", "")
GATEWAY_MCP_URL = os.environ.get("GATEWAY_MCP_URL", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

app = BedrockAgentCoreApp()
log = app.logger
_ac_identity = boto3.client("bedrock-agentcore", region_name=AWS_REGION)


def _safe_claims(jwt: str) -> dict:
    """Decode a JWT payload without signature verification.

    Returns a small subset of claims that are safe to log for learning
    (no signature, no full token — just descriptive claims used by the
    LEARNING_GUIDE to trace user identity through the OBO chain).
    """
    import base64
    import json as _json

    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        raw = _json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}
    return {k: raw.get(k) for k in ("iss", "aud", "azp", "appid", "oid", "sub", "scp", "ver", "exp")}


def _obo_user_to_gateway(user_token: str) -> str:
    """OBO #1: exchange T_user (aud=AgentApp) for T_gateway (aud=GatewayApp).

    Uses AgentCore Identity's two-call pattern:
      1. GetWorkloadAccessTokenForJWT wraps the user JWT in a workload token.
      2. GetResourceOauth2Token with ON_BEHALF_OF_TOKEN_EXCHANGE performs the
         actual exchange via the agent-actor credential provider, which
         authenticates as AgentApp to Entra.
    """
    log.info("OBOTRACE: OBO 1 start. Exchanging T_user for T_gateway via AgentCore Identity.")
    workload_token = _ac_identity.get_workload_access_token_for_jwt(
        workloadName=AGENT_WORKLOAD_NAME,
        userToken=user_token,
    )["workloadAccessToken"]

    t_gateway = _ac_identity.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=AGENT_OBO_PROVIDER_NAME,
        scopes=[GATEWAY_SCOPE],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        # Microsoft's OBO is RFC 7523 JWT-bearer grant PLUS this proprietary
        # parameter. Without it, Entra either rejects the request with 400
        # or does a regular jwt-bearer grant with different semantics.
        # AgentCore Identity does NOT auto-add this for CustomOauth2 with
        # JWT_AUTHORIZATION_GRANT the caller must pass it explicitly.
        customParameters={"requested_token_use": "on_behalf_of"},
    )["accessToken"]

    # Structured "hop complete" log line. Uses only ASCII (no #, no em-dash)
    # so `agentcore logs --query 'OBO 1 complete'` treats it as a plain term.
    # Used by LEARNING_GUIDE Chapter 3.
    claims = _safe_claims(t_gateway)
    log.info(
        "OBOTRACE: OBO 1 complete. T_gateway minted. aud=%s azp=%s oid=%s scp=%s ver=%s",
        claims.get("aud"),
        claims.get("azp") or claims.get("appid"),
        claims.get("oid"),
        claims.get("scp"),
        claims.get("ver"),
    )
    return t_gateway


@contextmanager
def _gateway_mcp_client(gateway_token: str) -> Iterator[MCPClient]:
    """Open a Strands MCPClient connected to the AgentCore Gateway.

    The Gateway's inbound auth requires a Bearer JWT audienced at GatewayApp;
    that's T_gateway, the output of OBO #1. We pass it as the Authorization
    header on every MCP call.
    """
    if not GATEWAY_MCP_URL:
        raise RuntimeError(
            "GATEWAY_MCP_URL is not set. Re-run deploy/03_patch_agentcore_json.py after creating the Gateway."
        )

    headers = {"Authorization": f"Bearer {gateway_token}"}

    def _connect():
        # streamablehttp_client returns the (read, write, ...) tuple Strands'
        # MCPClient expects; we wrap it so the MCPClient owns the lifecycle.
        return streamablehttp_client(GATEWAY_MCP_URL, headers=headers)

    client = MCPClient(_connect)
    with client:
        yield client


SYSTEM_PROMPT = """
You are an assistant that answers the user's questions about their own
Microsoft 365 profile (name, email, job title, office location, etc.).

You have access to tools provided by an AgentCore Gateway, including a
`getMyProfile` tool. To answer the user:

  1. Call `getMyProfile` (it takes no arguments — the Gateway already knows
     who the caller is via the token chain).
  2. Read the relevant fields from the returned profile.
  3. Answer in one or two sentences.

Do not fabricate information not present in the profile. Do not discuss
tokens, OBO, or AgentCore internals unless the user explicitly asks.
"""


@app.entrypoint
async def invoke(payload, context):
    """Runtime entrypoint.

    Async generator — errors are yielded as plain strings rather than
    returned, because `return <value>` inside an async generator is illegal.
    """
    log.info("Invoking agent for OBO use case 2 (Entra)")

    headers = context.request_headers or {}
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        yield "ERROR: Missing or malformed Authorization header"
        return
    user_jwt = auth.split(" ", 1)[1]

    # Structured log for LEARNING_GUIDE Chapter 2. Describes T_user by its
    # non-sensitive identity/audience claims. Prefix with OBOTRACE: so
    # deploy/show_obo_trace.py can find & dedupe these lines cleanly.
    # No credentials logged.
    u_claims = _safe_claims(user_jwt)
    log.info(
        "OBOTRACE: T_user received. aud=%s azp=%s oid=%s scp=%s ver=%s",
        u_claims.get("aud"),
        u_claims.get("azp") or u_claims.get("appid"),
        u_claims.get("oid"),
        u_claims.get("scp"),
        u_claims.get("ver"),
    )

    # OBO 1 (user to gateway).
    try:
        gateway_token = _obo_user_to_gateway(user_jwt)
    except ClientError as e:
        log.error("OBO 1 (user to gateway) failed: %s", e)
        yield f"ERROR: OBO 1 failed: {e}"
        return

    prompt = payload.get("prompt", "What is my display name?")

    # Open an MCP session to the Gateway and let the LLM drive tool calls.
    try:
        with _gateway_mcp_client(gateway_token) as mcp_client:
            # LEARNING_GUIDE Chapter 4 — first outbound call to Gateway.
            # Inside Gateway, OBO #2 will run against T_gateway.
            log.info("OBOTRACE: MCP session opened to Gateway. About to list tools.")
            tools = mcp_client.list_tools_sync()
            tool_names = [getattr(t, "tool_name", getattr(t, "name", repr(t))) for t in tools]
            log.info(
                "OBOTRACE: Gateway MCP tools discovered: %s (count=%d)",
                tool_names,
                len(tool_names),
            )

            agent = Agent(
                model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                system_prompt=SYSTEM_PROMPT,
                tools=tools,
            )

            stream = agent.stream_async(prompt)
            async for event in stream:
                if "data" in event and isinstance(event["data"], str):
                    yield event["data"]
    except Exception as e:
        # Surface MCP/Gateway errors to the caller so the BFF can log them.
        # Strands' MCPClient runs inside anyio TaskGroups which wrap the
        # real cause; peel the wrapper so the response includes what
        # actually failed (e.g., 401 from Gateway, connection refused, etc).
        def _unwrap(err, depth=0):
            if depth > 5:
                return err
            inner = getattr(err, "exceptions", None) or ((err.__cause__,) if err.__cause__ else ())
            if inner:
                return _unwrap(inner[0], depth + 1)
            return err

        root = _unwrap(e)
        log.error(
            "Gateway / MCP / agent error: outer=%s: %s | root_cause=%s: %s",
            type(e).__name__,
            e,
            type(root).__name__,
            root,
        )
        yield (f"ERROR: {type(e).__name__}: {e}\nroot cause: {type(root).__name__}: {root}")


if __name__ == "__main__":
    app.run()
