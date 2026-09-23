"""Automate the standard, API-documented parts of the Okta setup for the sample.

This provisions the pieces the Okta Management API fully supports:
  * the OIDC "login / caller" app the user signs into (public PKCE,
    client_secret, or private_key_jwt with a registered public key),
  * a test user, and
  * the app assignment.

It prints .env-ready values when done.

What it does NOT do (these are XAA / "Okta for AI Agents" EA features without
stable public Management API payloads at the time of writing):
  * enable the Cross App Access feature flag,
  * "Enable XAA" on the resource app's Resource Server tab,
  * register the AI Agent, its key, delegations, and resource connections.
Do those manually per the README ("Okta setup -> Option A").

Auth: uses an Okta API token (SSWS). Create one in the Admin Console under
Security -> API -> Tokens. Treat it like a password.

Usage:
  export OKTA_ORG_URL=https://your-tenant.okta.com
  export OKTA_API_TOKEN=...            # SSWS token
  python3 okta_setup.py --label "XAA Login (Agent0)" \
      --redirect-uri http://localhost:8080/callback \
      --auth-method private_key_jwt --jwk-file ./keys/okta_public_jwk.json \
      --create-user --user-email bob@example.com --user-password 'S3cret!pass'
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

# OAuth grant-type URI (RFC 8693) — not a credential.
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"  # nosec B105


def _client(base_url: str, token: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={
            "Authorization": f"SSWS {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        timeout=30,
    )


def _check(resp: httpx.Response, what: str) -> dict:
    if resp.status_code >= 400:
        raise SystemExit(f"{what} failed ({resp.status_code}): {resp.text}")
    return resp.json()


def create_oidc_app(
    client: httpx.Client,
    label: str,
    redirect_uris: list[str],
    auth_method: str,
    jwk: dict | None,
    with_token_exchange: bool,
    dry_run: bool,
) -> dict:
    application_type = "native" if auth_method == "none" else "web"
    grant_types = ["authorization_code", "refresh_token"]
    if with_token_exchange:
        grant_types.append(TOKEN_EXCHANGE_GRANT)

    oauth_client_settings: dict = {
        "redirect_uris": redirect_uris,
        "response_types": ["code"],
        "grant_types": grant_types,
        "application_type": application_type,
    }
    if auth_method == "private_key_jwt":
        if not jwk:
            raise SystemExit("--auth-method private_key_jwt requires --jwk-file")
        oauth_client_settings["jwks"] = {"keys": [jwk]}

    payload = {
        # "name" is the Okta app *template* key. For a custom OIDC client this
        # MUST be "oidc_client"; without it Okta rejects the create with
        # "Invalid signOnMode" / "Missing visibility" because it falls back to a
        # generic (SWA) app template that expects a settings.signOn object.
        "name": "oidc_client",
        "label": label,
        "signOnMode": "OPENID_CONNECT",
        # Some org validation ("mediated") requires an explicit visibility block.
        "visibility": {"autoSubmitToolbar": False, "hide": {"iOS": True, "web": True}},
        "credentials": {"oauthClient": {"token_endpoint_auth_method": auth_method}},
        "settings": {"oauthClient": oauth_client_settings},
    }

    if dry_run:
        print("[dry-run] POST /api/v1/apps\n" + json.dumps(payload, indent=2))
        return {"id": "DRYRUN", "credentials": {"oauthClient": {"client_id": "DRYRUN"}}}

    app = _check(client.post("/api/v1/apps", json=payload), "Create OIDC app")
    print(f"Created OIDC app: {app['id']} ({label})")
    return app


def create_user(client: httpx.Client, email: str, password: str, dry_run: bool) -> dict:
    payload = {
        "profile": {"firstName": "XAA", "lastName": "Tester", "email": email, "login": email},
        "credentials": {"password": {"value": password}},
    }
    if dry_run:
        print("[dry-run] POST /api/v1/users?activate=true\n" + json.dumps(payload, indent=2))
        return {"id": "DRYRUN"}
    user = _check(client.post("/api/v1/users?activate=true", json=payload), "Create user")
    print(f"Created user: {user['id']} ({email})")
    return user


def assign_app_to_user(client: httpx.Client, app_id: str, user_id: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] POST /api/v1/apps/{app_id}/users (user {user_id})")
        return
    _check(
        client.post(f"/api/v1/apps/{app_id}/users", json={"id": user_id}),
        "Assign app to user",
    )
    print("Assigned app to user")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="XAA Login (Agent0)")
    parser.add_argument("--redirect-uri", action="append", dest="redirect_uris")
    parser.add_argument(
        "--auth-method",
        choices=["none", "client_secret_basic", "private_key_jwt"],
        default="none",
        help="Token endpoint auth method for the app (none = public PKCE).",
    )
    parser.add_argument("--jwk-file", help="Public JWK JSON (for private_key_jwt).")
    parser.add_argument(
        "--with-token-exchange",
        action="store_true",
        help="Also grant the token-exchange grant (Option B: app doubles as the "
        "requesting client). Requires XAA enabled on the org.",
    )
    parser.add_argument("--create-user", action="store_true")
    parser.add_argument("--user-email")
    parser.add_argument(
        "--user-password",
        help="Test user password. Omit to be prompted securely (avoids leaking it via shell history / process list).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base_url = os.environ.get("OKTA_ORG_URL") or os.environ.get("OKTA_ISSUER")
    token = os.environ.get("OKTA_API_TOKEN")
    if not base_url:
        sys.exit("Set OKTA_ORG_URL (or OKTA_ISSUER) to your Okta org URL.")
    if not token and not args.dry_run:
        sys.exit("Set OKTA_API_TOKEN (an SSWS API token).")

    redirect_uris = args.redirect_uris or ["http://localhost:8080/callback"]
    jwk = json.loads(Path(args.jwk_file).read_text()) if args.jwk_file else None

    client = _client(base_url, token or "dry-run")

    app = create_oidc_app(
        client,
        label=args.label,
        redirect_uris=redirect_uris,
        auth_method=args.auth_method,
        jwk=jwk,
        with_token_exchange=args.with_token_exchange,
        dry_run=args.dry_run,
    )

    if args.create_user:
        if not args.user_email:
            sys.exit("--create-user requires --user-email")
        # Prefer prompting for the password over passing it on the command line
        # (CLI args land in shell history and the process list).
        user_password = args.user_password
        if not user_password and not args.dry_run:
            user_password = getpass.getpass("Password for the test user: ")
        if not user_password:
            sys.exit("--create-user requires a password (pass --user-password or enter when prompted)")
        user = create_user(client, args.user_email, user_password, args.dry_run)
        assign_app_to_user(client, app["id"], user["id"], args.dry_run)

    # For Okta OIDC apps the client_id equals the app id. Read it from the app
    # id (independent of the credentials block) so we never log a value derived
    # from the structure that also carries the client secret.
    client_id = app.get("id", "<app client_id>")
    has_secret = bool(app.get("credentials", {}).get("oauthClient", {}).get("client_secret"))

    print("\n--- Add to scripts/.env ---")
    print(f"OKTA_LOGIN_CLIENT_ID={client_id}")
    if has_secret:
        # Do NOT print the client secret (avoid writing secrets to stdout/logs).
        # Retrieve it from the Okta Admin Console: Applications -> this app ->
        # General -> Client Credentials, and paste it into OKTA_LOGIN_CLIENT_SECRET.
        print("OKTA_LOGIN_CLIENT_SECRET=<copy from Okta Admin Console; not printed>")
    print(
        "\nNote: this app is the login/caller. Register the AI Agent separately\n"
        "(README -> Okta setup) and set OKTA_CLIENT_ID to its AI Agent ID."
    )


if __name__ == "__main__":
    main()
