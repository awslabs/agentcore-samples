"""Test MCP server with Keycloak DCR authentication + PKCE"""
import base64
import hashlib
import json
import os
import secrets
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
import requests
from dotenv import load_dotenv

load_dotenv()

# Keycloak configuration
KEYCLOAK_URL = os.environ["KEYCLOAK_URL"]
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "main")
INITIAL_ACCESS_TOKEN = os.environ["INITIAL_ACCESS_TOKEN"]

# AgentCore configuration
REGION = os.environ.get("REGION", "us-east-1")
AGENT_ARN = os.environ["AGENT_ARN"]

CALLBACK_PORT = int(os.environ.get("CALLBACK_PORT", "3030"))
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"


class CallbackHandler(BaseHTTPRequestHandler):
    callback_data = {"code": None}

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            CallbackHandler.callback_data["code"] = query["code"][0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>OK! Close this window.</h1>")

    def log_message(self, *args):
        pass


def register_client_dcr() -> str:
    """Register client via DCR"""
    client_id = f"mcp-test-{datetime.now().strftime('%H%M%S')}"
    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/clients-registrations/default"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {INITIAL_ACCESS_TOKEN}", "Content-Type": "application/json"},
        json={"clientId": client_id, "redirectUris": [REDIRECT_URI], "publicClient": True},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["clientId"]


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge"""
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return code_verifier, code_challenge


def get_token_via_browser(client_id: str) -> str:
    """Get token via browser OAuth flow with PKCE"""
    # Generate PKCE
    code_verifier, code_challenge = generate_pkce()
    
    CallbackHandler.callback_data = {"code": None}
    server = HTTPServer(("localhost", CALLBACK_PORT), CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    auth_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth?" + urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid",
        "kc_idp_hint": "google",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    print("🌐 Opening browser for login...")
    print("🔐 Using PKCE for security")
    webbrowser.open(auth_url)

    thread.join(timeout=120)
    server.server_close()

    code = CallbackHandler.callback_data["code"]
    if not code:
        raise Exception("No auth code received")

    # Exchange code for token with code_verifier
    resp = requests.post(
        f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def call_mcp(access_token: str, method: str, params: dict = None):
    """Call MCP server with Bearer token"""
    encoded_arn = quote(AGENT_ARN, safe="")
    mcp_url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1})
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {access_token}",
    }

    response = httpx.post(mcp_url, content=payload, headers=headers, timeout=30)

    if response.status_code != 200:
        return {"error": f"{response.status_code}: {response.text}"}

    for line in response.text.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    return {"error": response.text}


def main():
    print("📝 Registering client via DCR...")
    client_id = register_client_dcr()
    print(f"✅ Client: {client_id}")

    access_token = get_token_via_browser(client_id)
    print(f"✅ Token obtained!")

    # Initialize
    print("\n=== Initialize ===")
    result = call_mcp(access_token, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    })
    if "error" in result:
        print(f"❌ Error: {result['error']}")
        return
    print(f"Server: {result.get('result', {}).get('serverInfo', {})}")

    # List tools
    print("\n=== Tools ===")
    result = call_mcp(access_token, "tools/list")
    for tool in result.get("result", {}).get("tools", []):
        print(f"  - {tool['name']}: {tool['description']}")

    # Test tool
    print("\n=== Test: add_numbers(10, 20) ===")
    result = call_mcp(access_token, "tools/call", {"name": "add_numbers", "arguments": {"a": 10, "b": 20}})
    print(f"Result: {result.get('result', {}).get('structuredContent', {}).get('result')}")


if __name__ == "__main__":
    main()
