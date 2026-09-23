"""
Sign in as yourself in a browser through AgentCore Identity's
USER_FEDERATION flow and obtain a user access token for the OBO demo.

This is the AgentCore-mediated 3LO pattern - user login flows through
AgentCore Identity, not a raw OAuth 2.0 auth_code call. The difference
in this sample is that AgentCore Identity authenticates to Entra's
token endpoint using PRIVATE_KEY_JWT (KMS-signed client assertion via
an X.509 cert on the login web app), rather than a client secret.

Flow:

  1. GetWorkloadAccessTokenForUserId  → workload access token bound to
     "me" (or whatever USER_ID_3LO is set to).
  2. GetResourceOauth2Token(oauth2Flow=USER_FEDERATION,
     provider=CLIENT_PROVIDER_NAME, resourceOauth2ReturnUrl=<local>)
     → AgentCore returns an authorizationUrl + sessionUri.
  3. Open the authorizationUrl in your default browser and sign in
     with your real Entra account.
  4. Entra redirects to AgentCore Identity's managed callback URL,
     which does the authorization_code exchange with Entra using
     PRIVATE_KEY_JWT - this is where the KMS sign call happens.
     AgentCore stores the resulting user tokens in its vault, then
     redirects to the local callback URL with a session_id.
  5. The local callback server captures the session_id.
  6. CompleteResourceTokenAuth binds the OAuth session to your userId.
  7. GetResourceOauth2Token(...) is called again (without
     forceAuthentication) and returns the actual access token.
  8. Writes the access token to .env as ENTRA_USER_JWT.

Prerequisites:
  - setup/00 through setup/04b have all been run.
  - The login web app's redirect_uris contains AgentCore's managed
    callback URL (setup/04b handles that, either via Graph PATCH or
    by printing manual instructions).

Usage:
    python get_entra_user_jwt.py
    python get_entra_user_jwt.py --scope "openid profile email User.Read offline_access"
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
import urllib.parse
import webbrowser
from pathlib import Path

import boto3
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"
DEFAULT_USER_ID = "me"
DEFAULT_DELEGATED_SCOPE_NAME = "obo_access"
# Entra's OIDC scopes plus the service app's OBO scope. Requesting the
# service app's scope makes the resulting access token audienced at
# api://<service-app-clientId> - which is what Entra requires for the
# OBO exchange (a Graph-audience token would fail with either an
# "Invalid Signature for bearerToken" from AgentCore Identity or
# AADSTS50013 from Entra during the exchange). offline_access is
# required for Entra to return a refresh token.
DEFAULT_OIDC_SCOPES = ["openid", "profile", "email", "offline_access"]


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
        description="Browser 3LO via AgentCore Identity USER_FEDERATION to mint an Entra user JWT."
    )
    parser.add_argument(
        "--scope",
        default=None,
        help=(
            "Space-separated scopes. Defaults to "
            "'openid profile email offline_access api://<serviceClientId>/obo_access'. "
            "Do NOT swap in a Microsoft Graph scope like User.Read - the OBO "
            "exchange requires the subject token's aud to be your API."
        ),
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help=(
            "User identifier passed to AgentCore Identity for session binding. Defaults to USER_ID_3LO env var or 'me'."
        ),
    )
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("CLIENT_PROVIDER_NAME")
    local_url = must_env("LOCAL_CALLBACK_URL")
    service_client_id = must_env("ENTRA_SERVICE_CLIENT_ID")
    scope_name = os.environ.get("ENTRA_DELEGATED_SCOPE_NAME") or DEFAULT_DELEGATED_SCOPE_NAME

    default_scopes = [
        *DEFAULT_OIDC_SCOPES,
        f"api://{service_client_id}/{scope_name}",
    ]
    scopes = args.scope.split() if args.scope else default_scopes
    user_id = args.user_id or os.environ.get("USER_ID_3LO") or DEFAULT_USER_ID

    data_client = boto3.client("bedrock-agentcore", region_name=region)

    print(f"Region:    {region}")
    print(f"Workload:  {workload_name}")
    print(f"Provider:  {provider_name}")
    print(f"User ID:   {user_id}")
    print(f"Callback:  {local_url}")
    print(f"Scopes:    {scopes}")
    print()

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
        print("  Sign in with your real Entra account (password + MFA if")
        print("  Conditional Access requires it).")
        webbrowser.open(auth_url)

        # 4. Wait for the callback.
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

    write_env_var("ENTRA_USER_JWT", access_token)

    claims = decode_jwt_claims(access_token)
    print()
    print("=" * 70)
    print("  User access token issued by Entra")
    print("=" * 70)
    print(f"  Preview: {access_token[:40]}...{access_token[-10:]}")
    print()
    claim_meaning = {
        "iss": "Entra issuer - sts.windows.net/<tenant>/",
        "aud": "Audience - the API this token is for (Graph or a custom API)",
        "appid": "AppId - the LOGIN web app that requested this token",
        "appidacr": "App auth method - '2' means certificate (PRIVATE_KEY_JWT)",
        "idtyp": "'user' - delegated token, not app-only",
        "name": "End user's display name",
        "upn": "End user's UPN (User Principal Name)",
        "preferred_username": "Human-readable username",
        "oid": "Object ID of the end user in this tenant",
        "sub": "Subject - hash-based user id in the context of this app",
        "scp": "Delegated scopes granted",
        "iat": "Issued at (unix time)",
        "exp": "Expires at (unix time)",
    }
    print("  Decoded claims:")
    for key, meaning in claim_meaning.items():
        if key in claims:
            print(f"    {key:<20}: {claims[key]}")
            print(f"    {'':<20}  ↳ {meaning}")
    print()
    print("  What just happened:")
    print("    1. AgentCore Identity built an authorize URL and opened it")
    print("       in your browser.")
    print("    2. Entra authenticated you (password / MFA per your tenant's")
    print("       Conditional Access rules).")
    print("    3. Entra redirected to AgentCore's managed callback URL with")
    print("       an authorization code.")
    print("    4. AgentCore built a JWT client assertion for the LOGIN web")
    print("       app (iss=sub=login app's appId, aud=Entra /token), signed")
    print("       it via kms:Sign, and POSTed to Entra's /token with")
    print("       grant_type=authorization_code + client_assertion. Same")
    print("       PRIVATE_KEY_JWT dance as M2M, just with a user-context")
    print("       grant type.")
    print("    5. Entra returned the access token above (plus a refresh")
    print("       token via offline_access). AgentCore stored them in its")
    print("       vault and returned the access token to this script.")
    print()
    print("  Written to .env:")
    print("    ENTRA_USER_JWT=<the JWT above>")
    print()
    print("  Note that appid here is the LOGIN web app, not the service app.")
    print("  That's expected: this token was minted by the browser-3LO flow,")
    print("  which runs under the web app. The OBO exchange in the next step")
    print("  swaps this token for one whose appid is the SERVICE app.")
    print()
    print("  Next step:")
    print("    python outbound_private_key_jwt_obo.py")


if __name__ == "__main__":
    main()
