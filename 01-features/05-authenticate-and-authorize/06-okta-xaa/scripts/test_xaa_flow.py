"""Milestone A: prove the Okta Cross App Access (ID-JAG) flow end-to-end from the
CLI, using the productized **AI Agents** model (the resource is an Okta **custom
Authorization Server**).

Steps:
  0. Interactive OIDC login (auth code + PKCE) at the ORG server -> user ID token.
  1. token-exchange at the ORG server:            ID token -> ID-JAG
  2. jwt-bearer at the resource custom AS:         ID-JAG   -> access token
  3. call the resource API /todos with that access token.

Both legs authenticate as the **AI Agent** (`wlp…` id) with the SAME
`private_key_jwt` key registered on the agent in Okta. There is no separate
resource client. See the README ("Okta setup") for the full walkthrough.

Usage:
    cp .env.example .env    # fill in values
    python3 test_xaa_flow.py
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import os
import secrets
import threading
import urllib.parse
import webbrowser

import httpx
import jwt
from client_auth import apply_client_auth
from dotenv import load_dotenv

load_dotenv()

OKTA_ISSUER = os.environ["OKTA_ISSUER"].rstrip("/")

# The OIDC login app the user signs into (its client_id is the ID token `aud`).
LOGIN_CLIENT_ID = os.environ["OKTA_LOGIN_CLIENT_ID"]
LOGIN_CLIENT_SECRET = os.environ.get("OKTA_LOGIN_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("REDIRECT_URI", "http://localhost:8080/callback")

# The AI Agent (wlp...) that drives both token-exchange legs.
AGENT_CLIENT_ID = os.environ["OKTA_CLIENT_ID"]

# The resource's Okta custom Authorization Server + its audience, and the HTTP
# base URL of the resource API to call in step 3.
RESOURCE_AS_ISSUER = os.environ["RESOURCE_AS_ISSUER"].rstrip("/")
RESOURCE_API = os.environ.get("RESOURCE_API", "api://todo")
RESOURCE_API_URL = os.environ.get("RESOURCE_API_URL", "").rstrip("/")

SCOPES = os.environ.get("XAA_SCOPES", "todos.read")

# Client authentication for the AI Agent (both legs use the same key/kid).
CLIENT_AUTH_METHOD = os.environ.get("CLIENT_AUTH_METHOD", "private_key_jwt")
CLIENT_ASSERTION_ALG = os.environ.get("CLIENT_ASSERTION_ALG", "RS256")
OKTA_PRIVATE_KEY = os.environ.get("OKTA_PRIVATE_KEY", "")
OKTA_PRIVATE_KEY_PATH = os.environ.get("OKTA_PRIVATE_KEY_PATH", "")
OKTA_PRIVATE_KEY_KID = os.environ.get("OKTA_PRIVATE_KEY_KID", "")

# OAuth grant/token-type URIs (RFC 8693 / draft ID-JAG) — not credentials.
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"  # nosec B105
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"  # nosec B105
ID_JAG_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id-jag"  # nosec B105
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"  # nosec B105

ORG_AUTHORIZE_ENDPOINT = f"{OKTA_ISSUER}/oauth2/v1/authorize"
ORG_TOKEN_ENDPOINT = f"{OKTA_ISSUER}/oauth2/v1/token"
RESOURCE_TOKEN_ENDPOINT = f"{RESOURCE_AS_ISSUER}/v1/token"


def _agent_auth(token_endpoint: str):
    """private_key_jwt as the AI Agent -- same key/kid for both legs."""
    return apply_client_auth(
        method=CLIENT_AUTH_METHOD,
        client_id=AGENT_CLIENT_ID,
        client_secret="",  # nosec B106 - private_key_jwt; no secret used
        token_endpoint=token_endpoint,
        private_key=OKTA_PRIVATE_KEY,
        private_key_path=OKTA_PRIVATE_KEY_PATH,
        kid=OKTA_PRIVATE_KEY_KID,
        alg=CLIENT_ASSERTION_ALG,
    )


# --- Step 0: OIDC login with PKCE -------------------------------------------


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.code = params.get("code", [None])[0]
        _CallbackHandler.state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Login complete. Return to the terminal.</h2>")

    def log_message(self, *_a) -> None:
        return


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def login_and_get_id_token() -> str:
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode(
        {
            "client_id": LOGIN_CLIENT_ID,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    parsed = urllib.parse.urlparse(REDIRECT_URI)
    server = http.server.HTTPServer((parsed.hostname, parsed.port), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    auth_url = f"{ORG_AUTHORIZE_ENDPOINT}?{query}"
    print(f"\nOpening browser for Okta login:\n  {auth_url}\n")
    webbrowser.open(auth_url)
    thread.join(timeout=300)

    if not _CallbackHandler.code:
        raise SystemExit("Did not receive an authorization code (login timed out?)")
    if _CallbackHandler.state != state:
        raise SystemExit("State mismatch -- possible CSRF, aborting")

    data = {
        "grant_type": "authorization_code",
        "code": _CallbackHandler.code,
        "redirect_uri": REDIRECT_URI,
        "client_id": LOGIN_CLIENT_ID,
        "code_verifier": verifier,
    }
    auth = (LOGIN_CLIENT_ID, LOGIN_CLIENT_SECRET) if LOGIN_CLIENT_SECRET else None
    resp = httpx.post(ORG_TOKEN_ENDPOINT, data=data, auth=auth, timeout=30)
    resp.raise_for_status()
    id_token = resp.json()["id_token"]
    who = jwt.decode(id_token, options={"verify_signature": False})
    print(f"Step 0 OK: id_token for {who.get('email') or who.get('sub')} (aud={who.get('aud')})")
    return id_token


# --- Step 1: id_token -> ID-JAG (ORG server) --------------------------------


def exchange_id_token_for_id_jag(id_token: str) -> str:
    data = {
        "grant_type": TOKEN_EXCHANGE_GRANT,
        "requested_token_type": ID_JAG_TOKEN_TYPE,
        "subject_token": id_token,
        "subject_token_type": ID_TOKEN_TYPE,
        "audience": RESOURCE_AS_ISSUER,
        "scope": SCOPES,
    }
    extra, auth = _agent_auth(ORG_TOKEN_ENDPOINT)
    data.update(extra)
    resp = httpx.post(ORG_TOKEN_ENDPOINT, data=data, auth=auth, timeout=30)
    if resp.status_code >= 400:
        raise SystemExit(f"Step 1 (ID-JAG) failed ({resp.status_code}): {resp.text}")
    id_jag = resp.json()["access_token"]
    claims = jwt.decode(id_jag, options={"verify_signature": False})
    print(
        f"Step 1 OK: ID-JAG (aud={claims.get('aud')}, sub={claims.get('sub')}, "
        f"scope={claims.get('scope') or claims.get('scp')})"
    )
    return id_jag


# --- Step 2: ID-JAG -> access token (resource custom AS) --------------------


def exchange_id_jag_for_access_token(id_jag: str) -> str:
    data = {"grant_type": JWT_BEARER_GRANT, "assertion": id_jag}
    extra, auth = _agent_auth(RESOURCE_TOKEN_ENDPOINT)
    data.update(extra)
    resp = httpx.post(RESOURCE_TOKEN_ENDPOINT, data=data, auth=auth, timeout=30)
    if resp.status_code >= 400:
        raise SystemExit(f"Step 2 (access token) failed ({resp.status_code}): {resp.text}")
    at = resp.json()["access_token"]
    claims = jwt.decode(at, options={"verify_signature": False})
    print(
        f"Step 2 OK: access token (iss={claims.get('iss')}, aud={claims.get('aud')}, "
        f"sub={claims.get('sub')}, scp={claims.get('scp') or claims.get('scope')})"
    )
    return at


# --- Step 3: call the resource API ------------------------------------------


def call_todo_api(access_token: str) -> None:
    if not RESOURCE_API_URL:
        print("Step 3 skipped: RESOURCE_API_URL not set (token exchange already proven).")
        return
    resp = httpx.get(f"{RESOURCE_API_URL}/todos", headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    if resp.status_code >= 400:
        raise SystemExit(f"Step 3 (/todos) failed ({resp.status_code}): {resp.text}")
    print("Step 3 OK: todos returned by the resource API:")
    for todo in resp.json().get("todos", []):
        print(f"  [{'x' if todo.get('done') else ' '}] {todo.get('title')}")


def main() -> None:
    id_token = login_and_get_id_token()
    id_jag = exchange_id_token_for_id_jag(id_token)
    access_token = exchange_id_jag_for_access_token(id_jag)
    call_todo_api(access_token)
    print("\nXAA (ID-JAG) flow complete.")


if __name__ == "__main__":
    main()
