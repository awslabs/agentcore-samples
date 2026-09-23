"""Demo: run a Strands agent against Microsoft Graph via AgentCore Gateway.

Starts an OAuth2 callback server, opens the Entra ID login page, captures
the bearer token, then connects a Strands agent to the gateway.

Requires MICROSOFT_TENANT_ID, MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET,
and OBO_GATEWAY_URL in environment or .env.

Usage:
    uv run python scripts/obo-token-exchange/invoke.py
"""

import base64
import json
import os
import subprocess
import sys
import urllib.parse
import webbrowser

# Add project root and script dir to path
project_root = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.dirname(__file__))

from token_callback_server import (  # noqa: E402
    get_callback_url,
    is_server_running,
    wait_for_server_ready,
    wait_for_token,
)


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def acquire_token(tenant_id, client_id, client_secret):
    """Start callback server, open browser, and capture Entra ID token."""
    if not is_server_running():
        server_cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "token_callback_server.py"),
            tenant_id,
            client_id,
            client_secret,
        ]
        subprocess.Popen(server_cmd)
        if not wait_for_server_ready():
            print("ERROR: Failed to start token callback server")
            sys.exit(1)
        print("Token callback server started")
    else:
        print("Token callback server already running")

    callback_url = get_callback_url()
    authorize_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?"
        f"client_id={client_id}&response_type=code&"
        f"redirect_uri={urllib.parse.quote(callback_url)}&"
        f"scope={urllib.parse.quote(f'api://{client_id}/access_as_user openid profile email')}"
    )

    print(f"Callback URL: {callback_url}")
    print("Opening browser for Microsoft sign-in...")
    webbrowser.open(authorize_url)
    print("Waiting for sign-in (up to 2 minutes)...")

    token = wait_for_token(timeout=120)
    if not token:
        print("ERROR: Timed out waiting for token. Run again.")
        sys.exit(1)

    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    print(f"\nToken captured for: {claims.get('name', 'unknown')}")
    print(f"  aud: {claims['aud']}")
    print(f"  scp: {claims.get('scp', 'N/A')}")
    return token


def main():
    load_env()

    tenant_id = get_required_env("MICROSOFT_TENANT_ID")
    client_id = get_required_env("MICROSOFT_CLIENT_ID")
    client_secret = get_required_env("MICROSOFT_CLIENT_SECRET")
    gateway_url = get_required_env("OBO_GATEWAY_URL")

    print("=" * 60)
    print("Step 1: Acquire Entra ID Token")
    print("=" * 60)
    bearer_token = acquire_token(tenant_id, client_id, client_secret)

    print("\n" + "=" * 60)
    print("Step 2: Run Strands Agent")
    print("=" * 60)
    print(f"Gateway URL: {gateway_url}\n")

    from strands import Agent
    from strands.tools.mcp import MCPClient
    from mcp.client.streamable_http import streamablehttp_client

    mcp_client = MCPClient(
        lambda: streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
    )

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        print(f"Discovered {len(tools)} MCP tools from Gateway:")
        for t in tools:
            print(f"  - {t.tool_name}")

        agent = Agent(
            model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            tools=tools,
            system_prompt=(
                "You are a Microsoft 365 assistant. Use the available tools "
                "to help users with their Microsoft profile, Outlook calendar, "
                "and email."
            ),
        )

        response = agent("What is my Microsoft profile information?")
        print(response)


if __name__ == "__main__":
    main()
