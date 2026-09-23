"""Demo: call the elicitation MCP server through the gateway passthrough target.

Runs the MCP streamable-http handshake against the gateway target, which
forwards to the AgentCore Runtime MCP server:

  1. initialize            -> capture the server's Mcp-Session-Id
  2. notifications/initialized (required before any other call)
  3. tools/list            -> print the server's tools

Two session ids travel on every request:
  - X-Amzn-Bedrock-AgentCore-Runtime-Session-Id pins the request to one runtime
    microvm (the target allowlists it as a request header).
  - Mcp-Session-Id is issued by the MCP server on initialize and echoed on
    later calls (the target allowlists it as a request and response header).

MCP responses are SSE (text/event-stream); this script parses the `data:`
frames. The runtime enforces CUSTOM_JWT inbound, so BEARER_TOKEN must be an
Entra ID token for the runtime app audience.

Requires in environment or .env:
  GATEWAY_URL    - shared gateway URL (written by deploy_gateway.py)
  TARGET_NAME    - passthrough target name (written by deploy.py)
  BEARER_TOKEN   - Entra ID token, aud = api://<runtime-client-id>

Usage:
    uv run python scripts/runtime-mcp-passthrough/invoke.py
"""

import json
import os
import sys
import uuid

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
    bearer_token = get_required_env("BEARER_TOKEN")

    url = f"{gateway_url}/{target_name}"
    # Pin every request in this session to the same runtime microvm.
    runtime_session_id = (uuid.uuid4().hex + uuid.uuid4().hex)[:40]

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": runtime_session_id,
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
                "clientInfo": {
                    "name": "runtime-mcp-passthrough-demo",
                    "version": "1.0",
                },
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
