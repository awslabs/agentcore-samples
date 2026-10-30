# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Assistant Agent — Strands-based agent deployed on Bedrock AgentCore.

Connects to an MCP server for role-gated tools (finance, HR, datetime).
Supports two auth modes:
  - User tokens: forwarded to MCP server for per-user role-based access
  - M2M tokens (CI/pipelines): shared cached token via Cognito client credentials
"""

import base64
import json
import logging
import os
import time
import urllib.parse

import boto3
import httpx
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.runtime.context import RequestContext
from botocore.exceptions import ClientError
from mcp.client.streamable_http import streamable_http_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands_tools import calculator

app = BedrockAgentCoreApp()

# Guardrail configuration is optional so the agent still runs when it is absent (local
# development, or a stack deployed before the guardrail existed). The model client only
# sends a guardrail configuration when both an id and a version are present, so they are
# treated as a pair rather than applied independently.
_guardrail_id = os.getenv("GUARDRAIL_ID")
_guardrail_version = os.getenv("GUARDRAIL_VERSION")
_model_kwargs = {"model_id": os.getenv("MODEL_ID", "au.anthropic.claude-haiku-4-5-20251001-v1:0")}
if _guardrail_id and _guardrail_version:
    _model_kwargs.update(
        guardrail_id=_guardrail_id,
        guardrail_version=_guardrail_version,
        # Record which policy intervened, so a blocked request can be diagnosed as an
        # attack or as an over-tight filter rather than looking like a model failure.
        guardrail_trace="enabled",
    )
model = BedrockModel(**_model_kwargs)

_m2m_mcp_client = None
_m2m_initialized = False


# --- MCP server connection config ---
# MCP_SERVER_ARN is set by CDK; used to build the HTTPS invocation URL
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
MCP_SERVER_ARN = os.getenv("MCP_SERVER_ARN")
MCP_OAUTH_SCOPE = os.getenv("MCP_OAUTH_SCOPE", "mcp/invoke")
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-2")
_encoded_arn = urllib.parse.quote(MCP_SERVER_ARN, safe="") if MCP_SERVER_ARN else None
MCP_URL = (
    f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com/runtimes/{_encoded_arn}/invocations?qualifier=DEFAULT"
    if _encoded_arn
    else None
)

# Simple in-memory cache for M2M tokens (refreshed before expiry)
_m2m_token_cache = {"token": None, "expires_at": 0}


async def get_mcp_token_m2m() -> str:
    """Get M2M token from Cognito using client credentials.

    Checks env var MCP_CLIENT_SECRET first; falls back to reading
    client_id/client_secret/token_endpoint from Secrets Manager (SECRET_ARN).
    Tokens are cached in-memory and refreshed 60s before expiry.
    """
    if _m2m_token_cache["token"] and time.time() < _m2m_token_cache["expires_at"]:
        return _m2m_token_cache["token"]

    client_id = os.getenv("MCP_CLIENT_ID")
    client_secret = os.getenv("MCP_CLIENT_SECRET")
    token_endpoint = os.getenv("MCP_TOKEN_ENDPOINT")

    if not client_secret and os.getenv("SECRET_ARN"):
        try:
            secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)
            secret_response = secrets_client.get_secret_value(SecretId=os.getenv("SECRET_ARN"))
            secret_data = json.loads(secret_response["SecretString"])
            client_id = secret_data.get("client_id", client_id)
            client_secret = secret_data["client_secret"]
            token_endpoint = secret_data.get("token_endpoint", token_endpoint)
        except (ClientError, KeyError, ValueError) as e:
            print(f"Failed to retrieve secret: {e}")

    if not all([client_id, client_secret, token_endpoint]):
        raise ValueError("MCP OAuth credentials not configured")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_endpoint,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": MCP_OAUTH_SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        data = response.json()

        _m2m_token_cache["token"] = data["access_token"]
        _m2m_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60

        return data["access_token"]


def _extract_bearer_token() -> str | None:
    """Extract Bearer token from BedrockAgentCoreContext headers."""
    from bedrock_agentcore.runtime import BedrockAgentCoreContext

    headers = BedrockAgentCoreContext.get_request_headers() or {}
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    return auth.removeprefix("Bearer ").strip() or None


def _is_user_token(token: str) -> bool:
    """Check if a JWT is a user token (has 'sub' claim) vs M2M.

    Cognito user tokens include a 'sub' claim; M2M client_credentials tokens don't.
    We decode without verification since AgentCore already validated the JWT.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return "sub" in claims
    except (ValueError, KeyError, IndexError):
        return False


def _make_mcp_client(token: str) -> MCPClient | None:
    """Create an MCPClient with the given Bearer token."""
    if not MCP_URL:
        return None
    try:
        http_client = httpx.AsyncClient(timeout=120, headers={"Authorization": f"Bearer {token}"})
        client = MCPClient(
            lambda hc=http_client: streamable_http_client(url=MCP_URL, http_client=hc),
            startup_timeout=120,
        )
        client.__enter__()
        return client
    except (httpx.HTTPError, OSError, RuntimeError) as e:
        print(f"Failed to initialize MCP client: {e}")
        return None


async def _get_m2m_mcp_client() -> MCPClient | None:
    """Get or initialize the shared M2M MCP client."""
    global _m2m_mcp_client, _m2m_initialized
    if _m2m_initialized:
        return _m2m_mcp_client
    _m2m_initialized = True

    if not MCP_SERVER_ARN:
        print("Warning: MCP_SERVER_ARN not configured, MCP tools unavailable")
        return None

    try:
        token = await get_mcp_token_m2m()
        _m2m_mcp_client = _make_mcp_client(token)
        return _m2m_mcp_client
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as e:
        print(f"Failed to initialize MCP client: {e}")
        return None


def get_tools(mcp_client: MCPClient | None):
    """Get all available tools."""
    tools = [calculator]
    print(f"Before retrieving MCP tools, total tools: {len(tools)}")
    logger.info(f"Before retrieving MCP tools, total tools: {len(tools)}")
    if mcp_client:
        try:
            tools.extend(mcp_client.list_tools_sync())
            print(f"Retrieved {len(tools) - 1} tools from MCP server")
            logger.info(f"Retrieved {len(tools) - 1} tools from MCP server")
        except (httpx.HTTPError, OSError, RuntimeError) as e:
            print(f"Failed to list MCP tools: {e}")
            logger.error(f"Failed to list MCP tools: {e}")
    return tools


@app.entrypoint
async def handle_request(payload, request_context: RequestContext = None):
    """Handle incoming requests.

    If the caller is a user (JWT has 'sub' claim), their token is forwarded
    to the MCP server so role-based tool access is enforced.
    For M2M callers (CI pipelines), a shared M2M token is used instead.
    """
    prompt = payload if isinstance(payload, str) else payload.get("prompt", str(payload))

    incoming_token = _extract_bearer_token()
    user_mcp_client = None

    if incoming_token and _is_user_token(incoming_token):
        # Per-request MCP client with user's token for role-based access
        user_http_client = httpx.AsyncClient(timeout=120, headers={"Authorization": f"Bearer {incoming_token}"})
        user_mcp_client = MCPClient(
            lambda hc=user_http_client: streamable_http_client(url=MCP_URL, http_client=hc),
            startup_timeout=120,
        )
        print("Using user token for MCP access")
        logger.info("Using user token for MCP access")
        user_mcp_client.__enter__()
        mcp_client = user_mcp_client
    else:
        print("No user token found; using shared M2M token for MCP access")
        logger.info("No user token found; using shared M2M token for MCP access")
        mcp_client = await _get_m2m_mcp_client()

    try:
        agent = Agent(
            model=model,
            tools=get_tools(mcp_client),
            system_prompt=(
                "You are an office assistant for internal staff. You have access to tools for "
                "arithmetic calculations, date and time lookups, stock price retrieval, and "
                "department headcount queries. Use the appropriate tool for each request. "
                "Respond concisely and professionally. If a request falls outside your "
                "available tools, say so clearly rather than guessing."
            ),
        )
        result = await agent.invoke_async(prompt)
        return str(result)
    finally:
        if user_mcp_client:
            try:
                user_mcp_client.__exit__(None, None, None)
            except Exception as e:
                # Teardown failure must not mask the request result, but it should
                # not be swallowed silently either.
                logger.warning(f"Failed to close user MCP client: {e}")


if __name__ == "__main__":
    app.run()
