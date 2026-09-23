"""Demo: call the Context7 MCP server through the gateway passthrough target.

Runs the MCP streamable-http handshake against the gateway target, which
forwards to the public Context7 MCP server:

  1. initialize            -> capture the server's Mcp-Session-Id
  2. notifications/initialized (required before any other call)
  3. tools/list            -> print the server's tools

The gateway uses authorizerType=NONE and JWT_PASSTHROUGH: it forwards the
caller's Authorization header to Context7 unchanged. A Context7 API key is
optional (CONTEXT7_API_KEY, a `ctx7sk-...` value); without it Context7 serves
unauthenticated requests at a lower rate limit, so this script omits the
Authorization header when no key is set.

MCP responses are SSE (text/event-stream); this script parses the `data:`
frames.

Requires in environment or .env:
  GATEWAY_URL       - shared gateway URL (written by deploy_gateway.py)
  TARGET_NAME       - passthrough target name (written by deploy.py)
Optional:
  CONTEXT7_API_KEY  - a `ctx7sk-...` key for higher rate limits

Usage:
    uv run python scripts/context7-passthrough/invoke.py
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
    api_key = os.environ.get("CONTEXT7_API_KEY")

    # Path-based routing: the gateway forwards /{target} to the Context7
    # endpoint (https://mcp.context7.com/mcp); no /mcp suffix on the gateway URL.
    url = f"{gateway_url}/{target_name}"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    # The gateway forwards Authorization to Context7. Only send it when a key is
    # configured; otherwise use the unauthenticated tier.
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    print(f"Gateway target: {url}")
    print(
        f"Context7 API key: {'set' if api_key else 'not set (unauthenticated tier)'}\n"
    )

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
                "clientInfo": {"name": "context7-passthrough-demo", "version": "1.0"},
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
