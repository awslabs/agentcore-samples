"""Demo: call the GitHub MCP server through the gateway passthrough target.

Runs the MCP streamable-http handshake against the gateway target, which
forwards to the GitHub MCP server (https://api.githubcopilot.com/mcp/):

  1. initialize            -> capture the server's Mcp-Session-Id
  2. notifications/initialized (required before any other call)
  3. tools/list            -> print the server's tools

The gateway uses authorizerType=NONE and JWT_PASSTHROUGH: it forwards the
caller's Authorization header to GitHub unchanged. The GitHub MCP server has no
unauthenticated tier, so a GitHub personal access token is required (sent as a
static `Authorization: Bearer` header; the server's OAuth flow is not used
through the gateway).

MCP responses are SSE (text/event-stream); this script parses the `data:`
frames.

Requires in environment or .env:
  GATEWAY_URL    - shared gateway URL (written by deploy_gateway.py)
  TARGET_NAME    - passthrough target name (written by deploy.py)
  GITHUB_TOKEN   - a GitHub personal access token with MCP access

Usage:
    uv run python scripts/github-mcp-passthrough/invoke.py
"""

import json
import os
import sys

import requests

PROTOCOL_VERSION = "2025-06-18"


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


def parse_sse_or_json(resp):
    """Return the JSON-RPC payload from an MCP response.

    The MCP server replies with SSE (``event: message`` / ``data: {...}``) or,
    less often, a plain JSON body. Handle both.
    """
    body = resp.text
    if "data:" in body:
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
    return json.loads(body) if body.strip() else {}


def main():
    load_env()

    gateway_url = get_required_env("GATEWAY_URL").rstrip("/")
    target_name = get_required_env("TARGET_NAME")
    github_token = get_required_env("GITHUB_TOKEN")

    # Path-based routing: the gateway forwards /{target} to the GitHub MCP
    # endpoint (https://api.githubcopilot.com/mcp/); no /mcp suffix on the URL.
    url = f"{gateway_url}/{target_name}"

    headers = {
        # Static PAT forwarded to GitHub by JWT_PASSTHROUGH (not the OAuth flow).
        "Authorization": f"Bearer {github_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }

    print(f"Gateway target: {url}\n")

    # 1. initialize
    print("--- initialize ---")
    init = requests.post(
        url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "github-passthrough-demo", "version": "1.0"},
            },
        },
        timeout=60,
    )
    init.raise_for_status()
    mcp_session_id = init.headers.get("mcp-session-id")
    print(f"  MCP session: {mcp_session_id}")
    if mcp_session_id:
        headers["Mcp-Session-Id"] = mcp_session_id

    # 2. notifications/initialized (required before any other call)
    print("--- notifications/initialized ---")
    note = requests.post(
        url,
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        timeout=60,
    )
    note.raise_for_status()

    # 3. tools/list
    print("--- tools/list ---\n")
    listed = requests.post(
        url,
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        timeout=60,
    )
    listed.raise_for_status()
    payload = parse_sse_or_json(listed)

    if "error" in payload:
        print(f"  Error: {payload['error']}")
        sys.exit(1)

    tools = payload.get("result", {}).get("tools", [])
    print(f"  {len(tools)} tools:")
    for t in tools:
        desc = (t.get("description") or "").splitlines()[0]
        print(f"    - {t['name']}: {desc}")


if __name__ == "__main__":
    main()
