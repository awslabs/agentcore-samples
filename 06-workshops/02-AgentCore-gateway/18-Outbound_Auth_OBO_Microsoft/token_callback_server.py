"""
Environment-aware OAuth2 token callback server for Entra ID.

Captures the authorization code from Entra ID and exchanges it for tokens.
Automatically detects local vs SageMaker Workshop Studio environments.

Usage:
    python3 token_callback_server.py <tenant-id> <client-id> <client-secret>
"""

import time
import json
import argparse
import logging
import socket

import uvicorn
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

PORT = 9090
CALLBACK_ENDPOINT = "/oauth2/callback"
PING_ENDPOINT = "/ping"
TOKEN_ENDPOINT = "/token"

logger = logging.getLogger(__name__)

# Global token storage
_captured_token = None


def _is_workshop_studio() -> bool:
    try:
        with open("/opt/ml/metadata/resource-metadata.json", "r") as f:
            json.load(f)
        return True
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def get_callback_base_url() -> str:
    """Get the browser-accessible base URL (environment-aware)."""
    if not _is_workshop_studio():
        return f"http://localhost:{PORT}"
    try:
        import boto3

        with open("/opt/ml/metadata/resource-metadata.json", "r") as f:
            data = json.load(f)
        client = boto3.client("sagemaker")
        resp = client.describe_space(DomainId=data["DomainId"], SpaceName=data["SpaceName"])
        return resp["Url"] + f"/proxy/{PORT}"
    except Exception:
        return f"http://localhost:{PORT}"


def get_callback_url() -> str:
    return f"{get_callback_base_url()}{CALLBACK_ENDPOINT}"


def is_server_running() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def get_captured_token() -> str:
    """Retrieve the captured token from a running server."""
    try:
        r = requests.get(f"http://localhost:{PORT}{TOKEN_ENDPOINT}", timeout=2)
        if r.status_code == 200:
            return r.json().get("access_token", "")
    except Exception:
        pass
    return ""


def wait_for_token(timeout=120) -> str:
    """Poll the server until a token is captured."""
    start = time.time()
    while time.time() - start < timeout:
        token = get_captured_token()
        if token:
            return token
        time.sleep(2)
    return ""


def wait_for_server_ready(timeout=30) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"http://localhost:{PORT}{PING_ENDPOINT}", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


class TokenCallbackServer:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.app = FastAPI()
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get(PING_ENDPOINT)
        async def ping():
            return {"status": "ok"}

        @self.app.get(TOKEN_ENDPOINT)
        async def get_token():
            return {"access_token": _captured_token or ""}

        @self.app.get(CALLBACK_ENDPOINT)
        async def callback(code: str = None, error: str = None, error_description: str = None):
            global _captured_token

            if error:
                from html import escape

                return HTMLResponse(
                    f"<h2>Error: {escape(error)}</h2><p>{escape(error_description or '')}</p>",
                    status_code=400,
                )

            if not code:
                raise HTTPException(status_code=400, detail="Missing code parameter")

            redirect_uri = get_callback_url()
            token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            r = requests.post(  # nosec B113
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": f"api://{self.client_id}/access_as_user openid profile email",
                },
            )
            tokens = r.json()

            if "error" in tokens:
                from html import escape

                return HTMLResponse(
                    f"<h2>Token exchange error: {escape(tokens['error'])}</h2>"
                    f"<p>{escape(tokens.get('error_description', ''))}</p>",
                    status_code=400,
                )

            _captured_token = tokens.get("access_token", "")
            print(f"\n{'=' * 60}")
            print("TOKEN RECEIVED")
            print(f"{'=' * 60}")
            print(f"\nFULL ACCESS TOKEN:\n{_captured_token}")
            print(f"\n{'=' * 60}")
            return HTMLResponse("<h2>✅ Token captured! Return to the notebook.</h2>", status_code=200)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tenant_id")
    parser.add_argument("client_id")
    parser.add_argument("client_secret")
    args = parser.parse_args()

    server = TokenCallbackServer(args.tenant_id, args.client_id, args.client_secret)
    host = "0.0.0.0" if _is_workshop_studio() else "127.0.0.1"  # nosec B104
    callback_url = get_callback_url()

    authorize_url = (
        f"https://login.microsoftonline.com/{args.tenant_id}/oauth2/v2.0/authorize?"
        f"client_id={args.client_id}&response_type=code&"
        f"redirect_uri={__import__('urllib.parse', fromlist=['quote']).quote(callback_url)}&"
        f"scope={__import__('urllib.parse', fromlist=['quote']).quote(f'api://{args.client_id}/access_as_user openid profile email')}"
    )

    print(f"\n🚀 Token callback server on {host}:{PORT}")
    print(f"📋 Callback URL: {callback_url}")
    print("\n📋 Open this URL in your browser to sign in:\n")
    print(authorize_url)
    print("\nWaiting for sign-in...\n")

    uvicorn.run(server.app, host=host, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
