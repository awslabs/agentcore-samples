"""Shared Cognito and Gateway authentication utilities."""

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
    """Retrieve a Cognito access token with the client-credentials flow."""
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
    """Create an authenticated MCP Streamable HTTP transport factory."""
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
