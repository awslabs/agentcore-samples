"""Interactive Okta sign-in: authorization code + PKCE, with private_key_jwt.

Shared by scripts/test_chain.py and anything else that needs a real user token. The
AI Agent's linked app has no client secret, so the code exchange presents a client
assertion signed with the same key the interceptor uses for the ID-JAG legs.

Returns the raw token response, so callers get BOTH the access token (invokes the
agent) and the ID token (the subject of ID-JAG leg 1).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _common import env, must_env

KEYS_DIR = Path(__file__).resolve().parent / "keys"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

_code: dict[str, str] = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _code.update({k: v[0] for k, v in params.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        ok = "code" in params
        self.wfile.write(
            b"<h2>Signed in. Return to the terminal.</h2>"
            if ok
            else b"<h2>No authorization code. Check the terminal.</h2>"
        )

    def log_message(self, *args) -> None:  # silence the default access log
        return


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def client_assertion(token_endpoint: str, client_id: str) -> str:
    """private_key_jwt assertion signed with the AI Agent's key.

    The linked app authenticates with Public/private key, so both the code exchange
    and the ID-JAG legs present an assertion rather than a client secret.
    """
    import jwt as pyjwt

    key_path = KEYS_DIR / "okta_private_key.pem"
    if not key_path.exists():
        sys.exit(f"missing {key_path} -- run scripts/gen_keypair.py")
    now = int(time.time())
    return pyjwt.encode(
        {
            "iss": client_id,
            "sub": client_id,
            "aud": token_endpoint,
            "iat": now,
            "exp": now + 300,
            "jti": secrets.token_urlsafe(16),
        },
        key_path.read_text(),
        algorithm="RS256",
        headers={"kid": must_env("AI_AGENT_KEY_KID")},
    )


def post_form(url: str, data: dict) -> tuple[int, dict]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:400]}


def sign_in(org: str, client_id: str, redirect_uri: str, scopes: str, as_issuer: str) -> dict:
    verifier = b64url(secrets.token_bytes(40))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    authorize = f"{as_issuer}/v1/authorize?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )

    port = urllib.parse.urlparse(redirect_uri).port or 8000
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("localhost", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"  opening a browser for sign-in (listening on :{port})")
    print("  complete Okta sign-in, including MFA, then come back here")
    webbrowser.open(authorize)
    deadline = time.time() + 300
    while "code" not in _code and "error" not in _code and time.time() < deadline:
        time.sleep(0.5)
    server.shutdown()

    if "error" in _code:
        sys.exit(f"  ✗ Okta returned error={_code.get('error')}: {_code.get('error_description')}")
    if "code" not in _code:
        sys.exit("  ✗ timed out waiting for the authorization code")
    if _code.get("state") != state:
        sys.exit("  ✗ state mismatch -- aborting")
    print("  ✓ got an authorization code")

    token_endpoint = f"{as_issuer}/v1/token"
    form = {
        "grant_type": "authorization_code",
        "code": _code["code"],
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "client_id": client_id,
    }
    if env("LOGIN_CLIENT_AUTH_METHOD", "private_key_jwt") == "private_key_jwt":
        form["client_assertion_type"] = CLIENT_ASSERTION_TYPE
        form["client_assertion"] = client_assertion(token_endpoint, client_id)
    else:
        form["client_secret"] = must_env("LOGIN_CLIENT_SECRET")

    status, body = post_form(token_endpoint, form)
    if status >= 400 or "id_token" not in body:
        sys.exit(f"  ✗ code exchange failed (HTTP {status}): {json.dumps(body)[:400]}")
    print("  ✓ exchanged the code for tokens")
    return body
