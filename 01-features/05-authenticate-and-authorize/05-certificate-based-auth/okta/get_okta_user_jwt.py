"""
Sign in as yourself in a browser through AgentCore Identity's
USER_FEDERATION flow and obtain a user access token for the OBO demo.

This is the same pattern as obo-training/3-examples/01-agent-to-
downstream/okta/local/generate_user_jwt.py - the user login is
mediated by AgentCore Identity, not a raw OAuth 2.0 auth_code call.
The difference in this sample is that AgentCore Identity authenticates
to Okta's token endpoint using PRIVATE_KEY_JWT (KMS-signed client
assertion), rather than a client secret.

Flow:

  1. GetWorkloadAccessTokenForUserId  → workload access token bound to
     "me" (or whatever USER_ID_3LO is set to).
  2. GetResourceOauth2Token(oauth2Flow=USER_FEDERATION,
     provider=CLIENT_PROVIDER_NAME, resourceOauth2ReturnUrl=<local>)
     → AgentCore returns an authorizationUrl + sessionUri.
  3. Open the authorizationUrl in your default browser and sign in
     with your real Okta account.
  4. Okta redirects to AgentCore Identity's managed callback URL,
     which does the authorization_code exchange with Okta using
     PRIVATE_KEY_JWT - this is where the KMS sign call happens.
     AgentCore stores the resulting user tokens in its vault, then
     redirects to the local callback URL with a session_id.
  5. The local callback server captures the session_id.
  6. CompleteResourceTokenAuth binds the OAuth session to your userId.
  7. GetResourceOauth2Token(...) is called again (without
     forceAuthentication) and returns the actual access token.
  8. Writes the access token to .env as OKTA_USER_JWT.

Prerequisites:
  - setup/01_create_okta_service_app.py         (service app + JWK)
  - setup/01a_configure_okta_auth_server.py     (scope + policy + rule)
  - setup/01b_create_okta_login_web_app.py      (Web app + JWK)
  - setup/03b_create_provider_client.py         (client provider +
    wires AgentCore callback to Web app redirect_uri + updates
    workload identity's allowed local callback URL)

Usage:
    python get_okta_user_jwt.py
    python get_okta_user_jwt.py --scope "openid profile email api:access"
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import boto3
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"
DEFAULT_USER_ID = "me"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def decode_jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, IndexError, TypeError):
        return {}


def write_env_var(name: str, value: str) -> None:
    text = ENV_FILE.read_text()
    pattern = rf"^{re.escape(name)}=.*$"
    replacement = f"{name}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        new_text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(new_text)


class _CallbackState:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.event = threading.Event()


def make_handler(state: _CallbackState, redirect_path: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != redirect_path:
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(parsed.query)
            state.session_id = (params.get("session_id") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if state.session_id:
                body = (
                    "<h1>Sign-in complete</h1>"
                    "<p>AgentCore Identity has captured your user token. "
                    "You can close this tab and return to the terminal.</p>"
                )
            else:
                body = "<h1>Missing session_id</h1><p>No session_id in the callback URL.</p>"
            self.wfile.write(body.encode("utf-8"))
            state.event.set()

        def log_message(self, fmt, *args):
            pass  # suppress default access-log noise

    return Handler


def start_callback_server(local_url: str) -> tuple[http.server.HTTPServer, _CallbackState]:
    parsed = urllib.parse.urlparse(local_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8081
    path = parsed.path or "/callback"

    state = _CallbackState()
    server = http.server.HTTPServer((host, port), make_handler(state, path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Browser 3LO via AgentCore Identity USER_FEDERATION to mint a user JWT."
    )
    parser.add_argument(
        "--scope",
        default=None,
        help="Space-separated scopes. Defaults to 'openid profile email <M2M_SCOPE>'.",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="User identifier passed to AgentCore Identity for session binding. Defaults to USER_ID_3LO env var or 'me'.",
    )
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("CLIENT_PROVIDER_NAME")
    local_url = must_env("LOCAL_CALLBACK_URL")
    m2m_scope = os.environ.get("M2M_SCOPE", "api:access")

    scopes = (args.scope or f"openid profile email {m2m_scope}").split()
    user_id = args.user_id or os.environ.get("USER_ID_3LO") or DEFAULT_USER_ID

    data_client = boto3.client("bedrock-agentcore", region_name=region)

    print(f"Region:    {region}")
    print(f"Workload:  {workload_name}")
    print(f"Provider:  {provider_name}")
    print(f"User ID:   {user_id}")
    print(f"Callback:  {local_url}")
    print(f"Scopes:    {scopes}")
    print()

    # Start the callback server before opening the browser.
    print("→ Starting local callback server...")
    server, state = start_callback_server(local_url)

    try:
        # 1. Workload access token bound to a user identifier.
        print("→ GetWorkloadAccessTokenForUserId...")
        workload_token = data_client.get_workload_access_token_for_user_id(
            workloadName=workload_name,
            userId=user_id,
        )["workloadAccessToken"]
        print("✓ Received workload access token")

        # 2. Start the 3LO flow - returns an authorizationUrl the user
        #    must visit in the browser.
        print()
        print("→ GetResourceOauth2Token (oauth2Flow=USER_FEDERATION, force=true)...")
        fed = data_client.get_resource_oauth2_token(
            workloadIdentityToken=workload_token,
            resourceCredentialProviderName=provider_name,
            scopes=scopes,
            oauth2Flow="USER_FEDERATION",
            resourceOauth2ReturnUrl=local_url,
            forceAuthentication=True,
        )
        auth_url = fed.get("authorizationUrl")
        session_uri = fed.get("sessionUri")
        if not (auth_url and session_uri):
            print(
                f"✗ Expected authorizationUrl + sessionUri from AgentCore, got:\n"
                f"  {json.dumps(fed, indent=2, default=str)}",
                file=sys.stderr,
            )
            sys.exit(1)

        # 3. Open the browser.
        print()
        print(f"→ Opening browser: {auth_url}")
        print("  Sign in with your real Okta account (MFA, Passkeys, whatever your")
        print("  tenant requires).")
        webbrowser.open(auth_url)

        # 4. Wait for the callback to arrive.
        print()
        print("Waiting for you to complete sign-in in the browser...")
        completed = state.event.wait(timeout=300)
        if not completed or not state.session_id:
            print("✗ Timed out or no session_id received.", file=sys.stderr)
            sys.exit(1)
        print(f"✓ Received session_id: {state.session_id[:16]}...")

        # 5. Bind the session to our userId.
        print()
        print("→ CompleteResourceTokenAuth (binds session to userId)...")
        data_client.complete_resource_token_auth(
            sessionUri=session_uri,
            userIdentifier={"userId": user_id},
        )
        print("✓ Session bound")

        # 6. Fetch a fresh workload token - the earlier one may have
        #    expired while waiting for the user.
        workload_token = data_client.get_workload_access_token_for_user_id(
            workloadName=workload_name,
            userId=user_id,
        )["workloadAccessToken"]

        # 7. Retrieve the actual access token.
        print()
        print("→ GetResourceOauth2Token (retrieve stored token, force=false)...")
        final = data_client.get_resource_oauth2_token(
            workloadIdentityToken=workload_token,
            resourceCredentialProviderName=provider_name,
            scopes=scopes,
            oauth2Flow="USER_FEDERATION",
            forceAuthentication=False,
            resourceOauth2ReturnUrl=local_url,
            sessionUri=session_uri,
        )
        access_token = final.get("accessToken")
        if not access_token:
            print(
                f"✗ No access_token in response: {json.dumps(final, indent=2, default=str)}",
                file=sys.stderr,
            )
            sys.exit(1)

    finally:
        server.shutdown()

    print()
    print(f"✓ Received user access token (length={len(access_token)})")

    write_env_var("OKTA_USER_JWT", access_token)
    print()

    claims = decode_jwt_claims(access_token)
    print("=" * 70)
    print("  User access token issued by Okta")
    print("=" * 70)
    print(f"  Preview: {access_token[:40]}...{access_token[-10:]}")
    print()
    print("  Decoded claims:")
    claim_meaning = {
        "iss": "Okta issuer - the Custom AS URL",
        "aud": "Audience - API this token is intended for",
        "cid": "Client ID - the Web app that requested this token",
        "uid": "Okta user ID - internal identifier",
        "sub": "Subject - your Okta login (the end user)",
        "scp": "Scopes granted",
        "preferred_username": "Human-readable username",
        "iat": "Issued at (unix time)",
        "exp": "Expires at (unix time)",
    }
    for key, meaning in claim_meaning.items():
        if key in claims:
            print(f"    {key:<20}: {claims[key]}")
            print(f"    {'':<20}  ↳ {meaning}")
    print()
    print("  What just happened:")
    print("    1. AgentCore identity built an authorize URL and opened it")
    print("       in your browser.")
    print("    2. Okta authenticated you (password / MFA per your Auth policy).")
    print("    3. Okta redirected to AgentCore's managed callback URL with an")
    print("       auth code.")
    print("    4. AgentCore built a JWT client assertion (iss=sub=Web app's")
    print("       client_id, aud=Okta token endpoint), signed it via kms:Sign,")
    print("       and POSTed to Okta's /token with grant_type=authorization_code")
    print("       + client_assertion. This is the same PRIVATE_KEY_JWT dance")
    print("       as M2M, just with a user-context grant type.")
    print("    5. Okta returned the access token above. AgentCore stored it in")
    print("       its vault and returned it to this script.")
    print()
    print("  Written to .env:")
    print("    OKTA_USER_JWT=<the JWT above>")
    print()
    print("  Next step:")
    print("    python outbound_private_key_jwt_obo.py")
    print("    (uses this token as the subject_token in the RFC 8693 exchange)")


if __name__ == "__main__":
    main()
