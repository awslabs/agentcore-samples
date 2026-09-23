"""
Shared Gateway authentication utilities for Web Search Tool demos.

Provides helpers for:
  - Obtaining OAuth tokens from Cognito (client_credentials flow)
  - Creating MCP Streamable HTTP transports authenticated against the Gateway

All demos in this folder share these utilities to avoid duplicating
auth boilerplate.
"""

import os

import requests
from mcp.client.streamable_http import streamablehttp_client


# ── Configuration ─────────────────────────────────────────────────────────────

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
GATEWAY_URL = os.getenv("AGENTCORE_GATEWAY_URL", "")
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
COGNITO_CLIENT_SECRET = os.getenv("COGNITO_CLIENT_SECRET", "")
COGNITO_SCOPE = os.getenv("COGNITO_SCOPE", "")


# ── Token retrieval ───────────────────────────────────────────────────────────


def get_oauth_token(
    domain: str = "",
    client_id: str = "",
    client_secret: str = "",
    scope: str = "",
    region: str = "",
) -> str:
    """Retrieve a fresh OAuth token from Cognito using client_credentials flow.

    Args:
        domain: Cognito domain prefix (e.g., "us-east-1abcdef12").
        client_id: Cognito app client ID.
        client_secret: Cognito app client secret.
        scope: OAuth scope string (e.g., "agentcore-websearch/invoke").
        region: AWS region for the Cognito endpoint.

    Returns:
        Access token string.
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

    url = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"
    response = requests.post(
        url,
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


# ── MCP transport factory ─────────────────────────────────────────────────────


def create_streamable_http_transport(gateway_url: str = "", **token_kwargs):
    """Create an MCP Streamable HTTP transport authenticated with a Bearer token.

    Args:
        gateway_url: The AgentCore gateway MCP endpoint URL.
        **token_kwargs: Passed to get_oauth_token() for credential overrides.

    Returns:
        A callable suitable for MCPClient initialization.
    """
    gateway_url = gateway_url or GATEWAY_URL
    if not gateway_url:
        raise ValueError("Gateway URL not configured. Set AGENTCORE_GATEWAY_URL environment variable.")

    token = get_oauth_token(**token_kwargs)

    def _transport():
        return streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {token}"},
        )

    return _transport
