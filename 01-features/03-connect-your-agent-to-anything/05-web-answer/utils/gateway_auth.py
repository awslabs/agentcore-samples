"""
Shared Cognito and Gateway authentication utilities for Web Answer examples.

The helpers in this module:
  - Request OAuth access tokens from a Cognito machine-to-machine app client
  - Create MCP Streamable HTTP transports authenticated to AgentCore Gateway

Credentials default to environment variables written by setup_gateway.py. Callers
may provide explicit values when they need to override that shared configuration.

Environment variables:
    AGENTCORE_GATEWAY_URL  — Gateway MCP endpoint
    COGNITO_DOMAIN         — Cognito domain prefix
    COGNITO_CLIENT_ID      — Cognito machine-to-machine app client ID
    COGNITO_CLIENT_SECRET  — Cognito app client secret
    COGNITO_SCOPE          — OAuth scope requested for Gateway invocation
    AWS_DEFAULT_REGION     — AWS region, defaulting to us-east-1
"""

import os
from contextlib import asynccontextmanager

import requests
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
GATEWAY_URL = os.getenv("AGENTCORE_GATEWAY_URL", "")
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
COGNITO_CLIENT_SECRET = os.getenv("COGNITO_CLIENT_SECRET", "")
COGNITO_SCOPE = os.getenv("COGNITO_SCOPE", "")


def get_oauth_token(
    domain: str = "",
    client_id: str = "",
    client_secret: str = "",
    scope: str = "",
    region: str = "",
) -> str:
    """Retrieve an OAuth access token using Cognito client credentials.

    Empty arguments fall back to the corresponding module-level environment
    configuration. A fresh token is requested on every call.

    Args:
        domain: Cognito hosted-UI domain prefix, without the AWS domain suffix.
        client_id: Cognito machine-to-machine app client ID.
        client_secret: Cognito app client secret.
        scope: OAuth scope to include in the token request.
        region: AWS region containing the Cognito user pool.

    Returns:
        The access token from Cognito's token endpoint.

    Raises:
        ValueError: If the domain, client ID, or client secret is missing.
        requests.HTTPError: If Cognito returns an unsuccessful HTTP response.
        KeyError: If a successful response does not contain ``access_token``.
    """
    domain = domain or COGNITO_DOMAIN
    client_id = client_id or COGNITO_CLIENT_ID
    client_secret = client_secret or COGNITO_CLIENT_SECRET
    scope = scope or COGNITO_SCOPE
    region = region or REGION

    if not all([domain, client_id, client_secret]):
        raise ValueError(
            "Cognito credentials not configured. Set COGNITO_DOMAIN, "
            "COGNITO_CLIENT_ID, and COGNITO_CLIENT_SECRET environment variables."
        )

    response = requests.post(
        f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def create_streamable_http_transport(gateway_url: str = "", **token_kwargs):
    """Create a bearer-authenticated MCP Streamable HTTP transport factory.

    The OAuth token is obtained when this function is called. The returned
    asynchronous context manager creates the MCP HTTP client and closes its
    resources when the caller exits the context.

    Args:
        gateway_url: AgentCore Gateway MCP endpoint. An empty value falls back
            to ``AGENTCORE_GATEWAY_URL``.
        **token_kwargs: Credential overrides forwarded to ``get_oauth_token``.

    Returns:
        An async context-manager factory suitable for ``MCPClient``.

    Raises:
        ValueError: If no Gateway URL is configured.
        requests.HTTPError: If Cognito rejects the OAuth token request.
    """
    gateway_url = gateway_url or GATEWAY_URL
    if not gateway_url:
        raise ValueError("Gateway URL not configured. Set AGENTCORE_GATEWAY_URL environment variable.")

    token = get_oauth_token(**token_kwargs)

    @asynccontextmanager
    async def _transport():
        http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        async with (
            http_client,
            streamable_http_client(
                gateway_url,
                http_client=http_client,
            ) as streams,
        ):
            yield streams

    return _transport
