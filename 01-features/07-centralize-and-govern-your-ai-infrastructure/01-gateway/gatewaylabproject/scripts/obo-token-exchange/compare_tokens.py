"""Compare user token vs OBO token to show the token exchange transformation.

Acquires an Entra ID user token, performs the OBO exchange manually, and
prints a side-by-side comparison of claims.

Requires MICROSOFT_TENANT_ID, MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET
in environment or .env.

Usage:
    uv run python scripts/obo-token-exchange/compare_tokens.py
"""

import base64
import json
import os
import subprocess
import sys
import urllib.parse
import webbrowser

import requests as req

# Add project root and old tutorial dir to path
project_root = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, project_root)

old_tutorial_dir = os.path.abspath(
    os.path.join(
        project_root,
        "..",
        "01-attach-targets",
        "mcp",
        "openapi-schema",
        "01-configure-auth",
        "old",
    )
)
sys.path.insert(0, old_tutorial_dir)

from token_callback_server import (  # noqa: E402
    get_callback_url,
    is_server_running,
    wait_for_server_ready,
    wait_for_token,
)


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


def decode_jwt(token):
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def acquire_token(tenant_id, client_id, client_secret):
    """Start callback server, open browser, and capture Entra ID token."""
    if not is_server_running():
        server_cmd = [
            sys.executable,
            os.path.join(old_tutorial_dir, "token_callback_server.py"),
            tenant_id,
            client_id,
            client_secret,
        ]
        subprocess.Popen(server_cmd)
        if not wait_for_server_ready():
            print("ERROR: Failed to start token callback server")
            sys.exit(1)
        print("Token callback server started")
    else:
        print("Token callback server already running")

    callback_url = get_callback_url()
    authorize_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?"
        f"client_id={client_id}&response_type=code&"
        f"redirect_uri={urllib.parse.quote(callback_url)}&"
        f"scope={urllib.parse.quote(f'api://{client_id}/access_as_user openid profile email')}"
    )

    print(f"Callback URL: {callback_url}")
    print("Opening browser for Microsoft sign-in...")
    webbrowser.open(authorize_url)
    print("Waiting for sign-in (up to 2 minutes)...")

    token = wait_for_token(timeout=120)
    if not token:
        print("ERROR: Timed out waiting for token. Run again.")
        sys.exit(1)
    return token


def main():
    load_env()

    tenant_id = get_required_env("MICROSOFT_TENANT_ID")
    client_id = get_required_env("MICROSOFT_CLIENT_ID")
    client_secret = get_required_env("MICROSOFT_CLIENT_SECRET")

    print("=" * 60)
    print("Step 1: Acquire Entra ID User Token")
    print("=" * 60)
    bearer_token = acquire_token(tenant_id, client_id, client_secret)
    user_claims = decode_jwt(bearer_token)
    print(f"Token captured for: {user_claims.get('name', 'unknown')}")

    print("\n" + "=" * 60)
    print("Step 2: Perform OBO Exchange")
    print("=" * 60)
    obo_resp = req.post(  # nosec B113
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "client_id": client_id,
            "client_secret": client_secret,
            "assertion": bearer_token,
            "scope": (
                "https://graph.microsoft.com/Calendars.Read "
                "https://graph.microsoft.com/Mail.Read "
                "https://graph.microsoft.com/User.Read"
            ),
            "requested_token_use": "on_behalf_of",
        },
    )

    obo_token = obo_resp.json().get("access_token", "")
    if not obo_token:
        print(f"OBO exchange failed: {obo_resp.json()}")
        sys.exit(1)

    obo_claims = decode_jwt(obo_token)

    print("\n" + "=" * 100)
    print(f"{'CLAIM':<20} {'USER TOKEN (app-scoped)':<40} {'OBO TOKEN (Graph-scoped)'}")
    print("=" * 100)
    for field in ["aud", "iss", "ver", "scp", "appid", "name", "email", "idp", "oid"]:
        uv = str(user_claims.get(field, "--"))[:38]
        ov = str(obo_claims.get(field, "--"))[:38]
        changed = " <- CHANGED" if uv != ov else ""
        print(f"{field:<20} {uv:<40} {ov}{changed}")

    # Show the delegation claim
    xms_st = obo_claims.get("xms_st", {})
    print("\nOBO delegation chain:")
    print(f"  xms_st.sub (original user): {xms_st.get('sub', 'N/A')}")
    print(f"  appid (acting app):         {obo_claims.get('appid', 'N/A')}")
    print(f"  app_displayname:            {obo_claims.get('app_displayname', 'N/A')}")


if __name__ == "__main__":
    main()
