#!/usr/bin/env python3
"""MCP Proxy Server - Dynamic proxy to AgentCore MCP with Keycloak auth.

Tools are fetched from remote server on first authenticated call.
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import webbrowser
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
import requests
from mcp.server.fastmcp import FastMCP

# Setup logging
logging.basicConfig(level=os.environ.get("FASTMCP_LOG_LEVEL", "WARNING"))
logger = logging.getLogger(__name__)

# Config from environment variables
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "main")
INITIAL_ACCESS_TOKEN = os.environ.get("INITIAL_ACCESS_TOKEN", "")
REGION = os.environ.get("REGION", "us-east-1")
AGENT_ARN = os.environ.get("AGENT_ARN", "")
CALLBACK_PORT = int(os.environ.get("CALLBACK_PORT", "3031"))  # Convert string to int
REDIRECT_URI = os.environ.get("REDIRECT_URI")

_cache = {"token": None}

class CallbackHandler(BaseHTTPRequestHandler):
    code = None
    def do_GET(self):
        CallbackHandler.code = parse_qs(urlparse(self.path).query).get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>OK! Close this window.</h1>")
    def log_message(self, *args): pass


def authenticate() -> str:
    client_id = f"mcp-proxy-{secrets.token_hex(4)}"
    requests.post(
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/clients-registrations/default",
        headers={"Authorization": f"Bearer {INITIAL_ACCESS_TOKEN}", "Content-Type": "application/json"},
        json={"clientId": client_id, "redirectUris": [REDIRECT_URI], "publicClient": True}, timeout=10,
    ).raise_for_status()

    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()

    CallbackHandler.code = None
    server = HTTPServer(("localhost", CALLBACK_PORT), CallbackHandler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    auth_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth?" + urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT_URI, "response_type": "code",
        "scope": "openid", "kc_idp_hint": "google",
        "code_challenge": code_challenge, "code_challenge_method": "S256",
    })
    webbrowser.open(auth_url)
    server.handle_request()
    server.server_close()

    if not CallbackHandler.code:
        raise Exception("Auth failed")

    resp = requests.post(
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token",
        data={"grant_type": "authorization_code", "client_id": client_id,
              "code": CallbackHandler.code, "redirect_uri": REDIRECT_URI, "code_verifier": code_verifier}, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def call_remote(method: str, params: dict = None):
    if not _cache["token"]:
        _cache["token"] = authenticate()

    url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{quote(AGENT_ARN, safe='')}/invocations?qualifier=DEFAULT"
    resp = httpx.post(url, content=json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}),
                      headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                               "Authorization": f"Bearer {_cache['token']}"}, timeout=30)
    for line in resp.text.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:]).get("result", {})
    raise Exception(f"Failed: {resp.text}")


# MCP Server with generic proxy tool
mcp = FastMCP("keycloak-mcp-proxy")


@mcp.tool()
def call_tool(tool_name: str, arguments: str = "{}") -> str:
    """Call any tool on the remote AgentCore MCP server.
    
    Args:
        tool_name: Name of the remote tool (e.g., 'add_numbers', 'multiply_numbers', 'greet_user')
        arguments: JSON string with tool arguments (e.g., '{"a": 10, "b": 20}')
    """
    logger.debug(f"[call_tool] Calling remote tool: {tool_name} with args: {arguments}")
    args = json.loads(arguments) if arguments else {}
    result = call_remote("tools/call", {"name": tool_name, "arguments": args})
    logger.debug(f"[call_tool] Result: {result}")
    return json.dumps(result.get("structuredContent", result))


@mcp.tool()
def list_tools() -> str:
    """List all available tools on the remote AgentCore MCP server."""
    logger.debug("[list_tools] Listing tools from remote AgentCore server...")
    result = call_remote("tools/list")
    tools = result.get("tools", [])
    logger.debug(f"[list_tools] Found {len(tools)} tools: {[t['name'] for t in tools]}")
    return json.dumps([{"name": t["name"], "description": t.get("description", "")} for t in tools])


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
