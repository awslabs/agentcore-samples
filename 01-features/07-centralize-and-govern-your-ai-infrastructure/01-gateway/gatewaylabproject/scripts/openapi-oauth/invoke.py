"""Demo: Invoke Zendesk MCP tools through AgentCore Gateway.

Lists tools and calls ListTickets via the gateway.
Uses Okta for inbound auth token (client credentials).

Requires GATEWAY_URL, OKTA_DISCOVERY_URL, OKTA_CLIENT_ID,
OKTA_CLIENT_SECRET in environment. OKTA_SCOPE defaults to "zendesk".

Usage:
    uv run python scripts/openapi-oauth/invoke.py
"""

import json
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_mcp_client import GatewayMCPClient


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_token(token_endpoint, client_id, client_secret, scope):
    response = requests.post(
        token_endpoint,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def main():
    load_env()

    gateway_url = os.environ.get("GATEWAY_URL")
    okta_discovery_url = os.environ.get("OKTA_DISCOVERY_URL")
    okta_client_id = os.environ.get("OKTA_CLIENT_ID")
    okta_client_secret = os.environ.get("OKTA_CLIENT_SECRET")
    okta_scope = os.environ.get("OKTA_SCOPE", "zendesk")

    if not gateway_url:
        print("ERROR: GATEWAY_URL not set. Export it or add to the script .env")
        sys.exit(1)
    if not okta_discovery_url:
        print("ERROR: OKTA_DISCOVERY_URL not set. Export it or add to the script .env")
        sys.exit(1)
    if not okta_client_id or not okta_client_secret:
        print("ERROR: OKTA_CLIENT_ID and OKTA_CLIENT_SECRET must be set.")
        sys.exit(1)

    discovery = requests.get(okta_discovery_url, timeout=10).json()
    okta_token_endpoint = discovery["token_endpoint"]

    def token_fn():
        return get_token(
            okta_token_endpoint, okta_client_id, okta_client_secret, okta_scope
        )

    mcp = GatewayMCPClient(gateway_url, token_fn)

    print(f"Gateway URL: {gateway_url}\n")

    print("=" * 60)
    print("tools/list")
    print("=" * 60)
    all_tools = mcp.list_all_tools()
    for t in all_tools:
        print(f"  {t['name']}: {t.get('description', '')}")

    print("\n" + "=" * 60)
    print("tools/call - CountTickets")
    print("=" * 60)
    tickets_tool = next(
        (t["name"] for t in all_tools if "CountTickets" in t["name"]),
        None,
    )
    if tickets_tool:
        print(json.dumps(mcp.call_tool(tickets_tool, {}), indent=2))
    else:
        print("  CountTickets tool not found")


if __name__ == "__main__":
    main()
