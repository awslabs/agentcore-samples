"""Spike 2: prove the ID-JAG legs, and settle what leg 1 accepts as subject_token.

    .venv/bin/python scripts/spikes/spike2_idjag_subject.py

Opens a browser for Okta sign-in (authorization code + PKCE) against the AI Agent's
linked app, then:

  leg 1a  subject_token = the ID token      (subject_token_type=id_token)
  leg 1b  subject_token = the access token  (subject_token_type=access_token)
  leg 2   whichever ID-JAG was minted, redeemed at the resource AS (jwt-bearer)

Why it matters: the customer's design stashes the *access* token and exchanges
that. `06-okta-xaa` proves leg 1 only with an **ID token**. If 1b fails, hop B must
carry the ID token, and -- because the gateway forwards only `Authorization` to an
upstream (see FINDINGS.md finding 3) -- it has to travel in the request body.

It also proves the two things the Management API cannot see: the **User access**
binding (leg 1) and the **Resource connection** (leg 2).

Nothing is created in AWS and nothing is written to .env. Tokens are never printed
in full -- only their claims.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "deploy"))
from _common import env, load_env, must_env, okta_org_url

KEYS_DIR = Path(__file__).resolve().parent.parent / "keys"
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ID_JAG = "urn:ietf:params:oauth:token-type:id-jag"
TT_ID = "urn:ietf:params:oauth:token-type:id_token"
TT_ACCESS = "urn:ietf:params:oauth:token-type:access_token"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

_code: dict[str, str] = {}


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def claims(token: str) -> dict:
    """Decode a JWT payload without verifying. Diagnostics only."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, IndexError, json.JSONDecodeError):
        return {}


def show(label: str, token: str) -> None:
    c = claims(token)
    scope = c.get("scp") or c.get("scope")
    print(f"    {label}: iss={c.get('iss')}")
    print(f"      aud={c.get('aud')}  cid={c.get('cid')}  sub={c.get('sub')}")
    print(f"      scp={scope}  exp_in={int(c.get('exp', 0) - time.time())}s  len={len(token)}")


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


def leg1(org: str, client_id: str, subject: str, subject_type: str, audience: str, scope: str):
    """ID-JAG exchange at the ORG authorization server."""
    token_endpoint = f"{org}/oauth2/v1/token"
    form = {
        "grant_type": TOKEN_EXCHANGE,
        "requested_token_type": ID_JAG,
        "subject_token": subject,
        "subject_token_type": subject_type,
        "audience": audience,
        "scope": scope,
        "client_id": client_id,
        "client_assertion_type": CLIENT_ASSERTION_TYPE,
        "client_assertion": client_assertion(token_endpoint, client_id),
    }
    return post_form(token_endpoint, form)


def leg2(as_issuer: str, client_id: str, id_jag: str):
    """Redeem the ID-JAG for a resource access token at the resource AS."""
    token_endpoint = f"{as_issuer}/v1/token"
    form = {
        "grant_type": JWT_BEARER,
        "assertion": id_jag,
        "client_id": client_id,
        "client_assertion_type": CLIENT_ASSERTION_TYPE,
        "client_assertion": client_assertion(token_endpoint, client_id),
    }
    return post_form(token_endpoint, form)


def main() -> None:
    load_env()
    org = okta_org_url()
    login_client = must_env("LOGIN_CLIENT_ID", "Run deploy/00_relink_login_app.py first.")
    agent_client = must_env("AI_AGENT_CLIENT_ID")
    as1 = must_env("AGENTCORE_AS_ISSUER")
    as2 = must_env("RESOURCE_AS_ISSUER")
    resource_scope = env("RESOURCE_SCOPE", "todos.read")
    agent_scope = env("SCOPE_AGENT_ACCESS", "agent.access")
    redirect_uri = env("FRONTEND_REDIRECT_URI", "http://localhost:8000/auth/callback")

    print(f"org            : {org}")
    print(f"login client   : {login_client}")
    print(f"agent client   : {agent_client}")
    print(f"resource AS    : {as2}")
    print(f"leg-1 audience : {as2}\n")

    print("--- sign in (authorization code + PKCE) ---")
    tokens = sign_in(org, login_client, redirect_uri, f"openid profile email {agent_scope}", as1)
    id_token, access_token = tokens["id_token"], tokens.get("access_token", "")
    show("id_token", id_token)
    if access_token:
        show("access_token", access_token)

    results: dict[str, str] = {}
    id_jags: dict[str, str] = {}

    print("\n--- leg 1a: subject_token = ID TOKEN ---")
    status, body = leg1(org, agent_client, id_token, TT_ID, as2, resource_scope)
    if status < 400 and "access_token" in body:
        results["id_token"] = "ACCEPTED"
        id_jags["id_token"] = body["access_token"]
        print(f"  ✓ ID-JAG minted (issued_token_type={body.get('issued_token_type')})")
        show("id-jag", body["access_token"])
    else:
        results["id_token"] = f"REJECTED ({status} {body.get('error')})"
        print(f"  ✗ HTTP {status}: {json.dumps(body)[:300]}")

    print("\n--- leg 1b: subject_token = ACCESS TOKEN ---")
    if not access_token:
        results["access_token"] = "SKIPPED (no access token issued)"
        print("  • no access token was issued at sign-in")
    else:
        status, body = leg1(org, agent_client, access_token, TT_ACCESS, as2, resource_scope)
        if status < 400 and "access_token" in body:
            results["access_token"] = "ACCEPTED"
            id_jags["access_token"] = body["access_token"]
            print(f"  ✓ ID-JAG minted (issued_token_type={body.get('issued_token_type')})")
            show("id-jag", body["access_token"])
        else:
            results["access_token"] = f"REJECTED ({status} {body.get('error')})"
            print(f"  ✗ HTTP {status}: {json.dumps(body)[:300]}")

    print("\n--- leg 2: redeem the ID-JAG at the resource AS ---")
    if not id_jags:
        print("  • no ID-JAG to redeem; leg 1 failed both ways")
    for kind, jag in id_jags.items():
        # ID-JAGs are single use, so each leg-1 result gets its own redemption.
        status, body = leg2(as2, agent_client, jag)
        if status < 400 and "access_token" in body:
            print(f"  ✓ from the {kind} ID-JAG -> resource access token")
            show("T_tool", body["access_token"])
        else:
            print(f"  ✗ from the {kind} ID-JAG: HTTP {status}: {json.dumps(body)[:300]}")

    print("\n================ SPIKE 2 VERDICT ================")
    for kind, verdict in results.items():
        print(f"  leg 1 with {kind:13} -> {verdict}")
    if results.get("access_token") == "ACCEPTED":
        print("\n  Hop B may carry the ACCESS token alone. The customer's original")
        print("  design works as drawn, and the BFF need not pass an ID token through.")
    elif results.get("id_token") == "ACCEPTED":
        print("\n  Hop B must carry the ID TOKEN. Because the gateway forwards only")
        print("  Authorization to an upstream, the ID token has to reach the")
        print("  interceptor in the request BODY, not a custom header.")
    else:
        print("\n  Neither worked. Check the User access binding (leg 1) and the")
        print("  Resource connection (leg 2) -- see IDP_SETUP_OKTA.md.")


if __name__ == "__main__":
    main()
