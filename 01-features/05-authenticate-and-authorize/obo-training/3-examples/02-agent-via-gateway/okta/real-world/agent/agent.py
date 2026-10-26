"""
Strands agent for the OBO Use Case 2 real-world example (Okta).

Runs on AgentCore Runtime. Inbound auth (configured in agentcore.json by
deploy/03_patch_agentcore_json.py) uses Okta OIDC — the Runtime validates
the inbound JWT (signature, issuer, audience, expiry) before this handler
runs.

Inside the handler we:
  1. Read the inbound user JWT from context.request_headers["Authorization"].
  2. Perform OBO #1 via AgentCore Identity using Okta's RFC 8693 Token
     Exchange grant:
       T_user (aud=api://default, cid=FrontendApp, scp=[...agent.access])
         -> T_gateway (aud=api://default, cid=AgentApp, scp=[gateway.access])
  3. Open an MCP client connection to AgentCore Gateway, presenting
     T_gateway as the Bearer credential.
  4. Hand the MCP client + tools to a Strands LLM Agent. The LLM picks the
     `callDownstreamApi` tool and calls it via MCP.
  5. The Gateway transparently performs OBO #2 (T_gateway -> T_downstream)
     and calls the mock downstream API. The agent never sees T_downstream.
  6. Stream the LLM's natural-language response back to the caller.

What this agent does NOT do:
  - Talk to the downstream API directly. There's no `requests.get(...)` on
    the downstream URL here.
  - Mint, store, or forward downstream-scoped tokens. That's the Gateway's
    job on the second hop.

Key Okta-vs-Entra differences from UC2 Entra's version:
  - customParameters carries `subject_token_type` (RFC 8693 required)
    instead of `requested_token_use` (Entra-specific).
  - The boto3 call includes `audiences=[OKTA_AUDIENCE]`, which is how the
    Okta token exchange call announces the audience. Missing this causes
    Okta to reject the exchange or mint a token with an unexpected aud.
  - Identity claim names are different in OBOTRACE lines: `sub` is the
    user's login (the seam claim that stays constant), `cid` is the actor
    that walks down the chain frontend -> agent -> gateway. Entra used
    `oid` and `azp`.

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


AGENT_WORKLOAD_NAME = os.environ.get("AGENT_WORKLOAD_NAME", "obo-uc2-okta-agent")
AGENT_OBO_PROVIDER_NAME = os.environ.get("AGENT_OBO_PROVIDER_NAME", "obo-uc2-okta-agent-actor")
GATEWAY_SCOPE = os.environ.get("GATEWAY_SCOPE", "gateway.access")
GATEWAY_MCP_URL = os.environ.get("GATEWAY_MCP_URL", "")
OKTA_AUDIENCE = os.environ.get("OKTA_AUDIENCE", "api://default")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

app = BedrockAgentCoreApp()
log = app.logger
_ac_identity = boto3.client("bedrock-agentcore", region_name=AWS_REGION)


# Claims we include in OBOTRACE lines. Okta's set is different from Entra's:
#   sub  — user identity (login). Stays constant across every T_* — the
#          seam claim for identity propagation.
#   cid  — client ID / actor. Rotates frontend -> agent -> gateway as
#          each layer performs an exchange.
#   uid  — Okta's internal user ID. Also stays constant (redundant with
#          sub for identification, but useful for auditing).
_TRACE_CLAIMS = ("iss", "aud", "cid", "sub", "uid", "scp", "exp")


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
    return {k: raw.get(k) for k in _TRACE_CLAIMS}


def _obo_user_to_gateway(user_token: str) -> str:
    """OBO #1: exchange T_user for T_gateway via Okta Token Exchange.

    Uses AgentCore Identity's two-call pattern:
      1. GetWorkloadAccessTokenForJWT wraps the user JWT in a workload token.
      2. GetResourceOauth2Token with ON_BEHALF_OF_TOKEN_EXCHANGE performs
         the actual exchange via the agent-actor credential provider, which
         authenticates as AgentApp to Okta.
    """
    log.info("OBOTRACE: OBO 1 start. Exchanging T_user for T_gateway via AgentCore Identity.")
    workload_token = _ac_identity.get_workload_access_token_for_jwt(
        workloadName=AGENT_WORKLOAD_NAME,
        userToken=user_token,
    )["workloadAccessToken"]

    # GATEWAY_SCOPE is a single custom scope name ("gateway.access") — pass
    # it as a single-element list because the SDK takes a list.
    scopes = [GATEWAY_SCOPE] if isinstance(GATEWAY_SCOPE, str) else list(GATEWAY_SCOPE)

    t_gateway = _ac_identity.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=AGENT_OBO_PROVIDER_NAME,
        scopes=scopes,
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        # RFC 8693 REQUIRED — declares what kind of token is being exchanged.
        # Without this, Okta returns HTTP 400 (invalid_request). AgentCore
        # Identity does NOT auto-add this for CustomOauth2 with
        # TOKEN_EXCHANGE grant; the caller must pass it explicitly.
        customParameters={
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
        # The audience of the resulting token. On Okta's default auth
        # server this is always OKTA_AUDIENCE (typically api://default) —
        # the server rejects requests for other audiences it doesn't know.
        # Missing this can produce tokens with an audience the Gateway's
        # customJwtAuthorizer won't accept.
        audiences=[OKTA_AUDIENCE],
    )["accessToken"]

    # Structured "hop complete" log line. ASCII only (no #, no em-dash) so
    # `agentcore logs --query 'OBO 1 complete'` treats it as a plain term.
    claims = _safe_claims(t_gateway)
    log.info(
        "OBOTRACE: OBO 1 complete. T_gateway minted. aud=%s cid=%s sub=%s scp=%s uid=%s",
        claims.get("aud"),
        claims.get("cid"),
        claims.get("sub"),
        claims.get("scp"),
        claims.get("uid"),
    )
    return t_gateway


@contextmanager
def _gateway_mcp_client(gateway_token: str) -> Iterator[MCPClient]:
    """Open a Strands MCPClient connected to the AgentCore Gateway.

    The Gateway's inbound auth requires a Bearer JWT audienced at
    OKTA_AUDIENCE (typically api://default) with the gateway.access scope —
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
You are an assistant that demonstrates the two-hop OBO chain by calling a
mock downstream REST API through AgentCore Gateway.

You have access to tools provided by an AgentCore Gateway, including a
`callDownstreamApi` tool. To answer the user:

  1. Call `callDownstreamApi` (it takes no arguments — the Gateway knows
     who the caller is via the token chain).
  2. The tool returns a JSON echo of the request that the mock downstream
     API received. The `headers.Authorization` field contains the Bearer
     token the Gateway forwarded — this is T_downstream, the token minted
     by OBO #2 inside the Gateway.
  3. Answer in one or two sentences confirming that the downstream API was
     reached and briefly describe what it echoed back (method, URL, and
     whether a Bearer token was present).

Do not attempt to decode the JWT — that's not something you're able to do
reliably. Do not repeat the raw token in your response (it's sensitive).
Do not discuss AgentCore, OBO, or IdP internals unless the user explicitly
asks. Keep the response short and factual.
"""


@app.entrypoint
async def invoke(payload, context):
    """Runtime entrypoint.

    Async generator — errors are yielded as plain strings rather than
    returned, because `return <value>` inside an async generator is illegal.
    """
    log.info("Invoking agent for OBO use case 2 (Okta)")

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
        "OBOTRACE: T_user received. aud=%s cid=%s sub=%s scp=%s uid=%s",
        u_claims.get("aud"),
        u_claims.get("cid"),
        u_claims.get("sub"),
        u_claims.get("scp"),
        u_claims.get("uid"),
    )

    # OBO 1 (user to gateway).
    try:
        gateway_token = _obo_user_to_gateway(user_jwt)
    except ClientError as e:
        log.error("OBO 1 (user to gateway) failed: %s", e)
        yield f"ERROR: OBO 1 failed: {e}"
        return

    prompt = payload.get("prompt", "Call the downstream API and confirm it responded.")

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
