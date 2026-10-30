"""Automate the Microsoft Entra ID setup for the consent-portal sample (Azure CLI).

Two app registrations, not three — this sample uses passthrough (the agent
forwards the user's token to the gateway as-is), so there is no agent app:

  1. Gateway resource app (agentcore-consent-github-gateway)
       - identifierUris: api://<appId>
       - api.requestedAccessTokenVersion: 2  (v2 tokens; the gateway's and
         runtime's /v2.0/ discovery URL fails iss validation without it)
       - Expose-an-API scope: access_as_user
       - ALSO the consent portal's login client: a client secret, plus its own
         access_as_user permission granted and admin-consented to itself.

     Why the portal signs in through this same app: under Entra the `sub`
     claim is a pairwise identifier keyed on the app a token is issued FOR.
     Consent granted on the portal is stored under the portal login's `sub`,
     and looked up under the `sub` the gateway resolves from the caller's
     token. Both tokens here are issued for this one resource app, so the two
     `sub` values are identical by construction and consent binds correctly.

  2. Frontend app (agentcore-consent-github-frontend)
       - web redirect: http://localhost:8000/auth/callback + a client secret
       - delegated permission to the resource app's access_as_user,
         granted + admin-consented (so sign-in shows no extra consent screen)

Writes to .env: TENANT_ID, GATEWAY_CLIENT_ID, PORTAL_CLIENT_ID (== gateway),
PORTAL_CLIENT_SECRET, FRONTEND_CLIENT_ID, FRONTEND_CLIENT_SECRET,
IDP_DISCOVERY_URL, IDP_AUDIENCE, GATEWAY_SCOPE, PORTAL_SCOPES, IDP=entra.

The portal's real redirect URI cannot be registered yet — the portal URL does
not exist until deploy/02_create_portal.py creates the portal. That script
registers it for you.

Prerequisites: Azure CLI >= 2.50, signed in (`az login`) as a user who can
create app registrations and grant admin consent.

Run from the sample root:
    python deploy/00_create_entra_apps.py
Re-running is safe: apps are reused by display name and secrets are only
minted when .env is missing them (pass --rotate-secrets to force).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ENV_PATH, save_env

APP_NAMES = {
    "gateway": "agentcore-consent-github-gateway",
    "frontend": "agentcore-consent-github-frontend",
}
REDIRECT_URI = "http://localhost:8000/auth/callback"
SIGN_IN_AUDIENCE = "AzureADMyOrg"
SCOPE_VALUE = "access_as_user"


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def az(*args: str, check: bool = True):
    cmd = ["az", *args]
    if "--output" not in args:
        cmd += ["--output", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        if check:
            die(f"`{' '.join(cmd)}` failed with exit {proc.returncode}:\n{proc.stderr.strip() or proc.stdout.strip()}")
        return None
    if proc.stdout.strip():
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout.strip()
    return None


def graph_get(path: str) -> dict:
    return az("rest", "--method", "GET", "--url", f"https://graph.microsoft.com/v1.0{path}")


def graph_patch(path: str, body: dict) -> None:
    az(
        "rest",
        "--method",
        "PATCH",
        "--url",
        f"https://graph.microsoft.com/v1.0{path}",
        "--headers",
        "Content-Type=application/json",
        "--body",
        json.dumps(body),
    )


def create_or_get_app(name: str, *, redirect_uri: str | None = None) -> dict:
    apps = az("ad", "app", "list", "--display-name", name)
    if apps:
        app = apps[0]
        print(f"  • App exists: {name} (appId={app['appId']})")
        if redirect_uri:
            uris = (app.get("web", {}) or {}).get("redirectUris", []) or []
            if redirect_uri not in uris:
                az("ad", "app", "update", "--id", app["appId"], "--web-redirect-uris", redirect_uri)
                print(f"    ✓ Set redirect URI: {redirect_uri}")
        return app
    args = ["ad", "app", "create", "--display-name", name, "--sign-in-audience", SIGN_IN_AUDIENCE]
    if redirect_uri:
        args += ["--web-redirect-uris", redirect_uri]
    app = az(*args)
    print(f"  ✓ Created app: {name} (appId={app['appId']})")
    # The resource app also needs a service principal so permissions can be
    # granted against it. `az ad sp create` errors if one exists; tolerate that.
    az("ad", "sp", "create", "--id", app["appId"], check=False)
    return app


def ensure_v2_access_tokens(object_id: str) -> None:
    """Force v2 access tokens. Without this Entra issues v1-style tokens whose
    iss (sts.windows.net) fails the gateway's and runtime's /v2.0/ discovery
    check with "Claim 'iss' value mismatch"."""
    app = graph_get(f"/applications/{object_id}")
    if (app.get("api") or {}).get("requestedAccessTokenVersion") == 2:
        print("    • api.requestedAccessTokenVersion already 2")
        return
    graph_patch(f"/applications/{object_id}", {"api": {"requestedAccessTokenVersion": 2}})
    print("    ✓ Set api.requestedAccessTokenVersion = 2")


def ensure_access_as_user_scope(object_id: str) -> str:
    app = graph_get(f"/applications/{object_id}")
    scopes = list((app.get("api") or {}).get("oauth2PermissionScopes") or [])
    for s in scopes:
        if s.get("value") == SCOPE_VALUE:
            print(f"    • Scope already exists: {SCOPE_VALUE}")
            return s["id"]
    scope_id = str(uuid.uuid4())
    scopes.append(
        {
            "adminConsentDescription": "Allows the calling application to invoke this API as the signed-in user.",
            "adminConsentDisplayName": "Access as the signed-in user",
            "id": scope_id,
            "isEnabled": True,
            "type": "User",
            "userConsentDescription": None,
            "userConsentDisplayName": None,
            "value": SCOPE_VALUE,
        }
    )
    graph_patch(f"/applications/{object_id}", {"api": {"oauth2PermissionScopes": scopes}})
    print(f"    ✓ Added scope: {SCOPE_VALUE}")
    return scope_id


def add_permission_and_grant(consumer_app_id: str, api_app_id: str, scope_id: str) -> None:
    """Declare the delegated permission, grant it, and admin-consent it.

    All three are needed: without the explicit `permission grant`, the consent
    is declared but never activated, and tokens fail with a consent-required
    error even though admin-consent reported success.
    """
    az(
        "ad",
        "app",
        "permission",
        "add",
        "--id",
        consumer_app_id,
        "--api",
        api_app_id,
        "--api-permissions",
        f"{scope_id}=Scope",
        check=False,
    )
    az(
        "ad",
        "app",
        "permission",
        "grant",
        "--id",
        consumer_app_id,
        "--api",
        api_app_id,
        "--scope",
        SCOPE_VALUE,
        check=False,
    )
    proc = subprocess.run(
        ["az", "ad", "app", "permission", "admin-consent", "--id", consumer_app_id],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        print(f"    ✓ Granted + admin-consented {SCOPE_VALUE} for {consumer_app_id}")
    else:
        err = (proc.stderr or proc.stdout).strip().splitlines()
        print(
            f"    ⚠ admin-consent failed: {err[-1] if err else 'unknown'}\n"
            f"      Ask a Global Admin to run:\n"
            f"        az ad app permission admin-consent --id {consumer_app_id}",
            file=sys.stderr,
        )


def env_value_is_placeholder(key: str) -> bool:
    if not ENV_PATH.exists():
        return True
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line[len(key) + 1 :].strip() in ("", "replace-me", "REPLACE_ME")
    return True


def reset_client_secret(app_id: str, label: str) -> str:
    result = az(
        "ad",
        "app",
        "credential",
        "reset",
        "--id",
        app_id,
        "--display-name",
        label,
        "--years",
        "1",
        "--append",
    )
    return result["password"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate-secrets",
        action="store_true",
        help="Mint fresh client secrets even if .env already has values.",
    )
    args = parser.parse_args()

    if not shutil.which("az"):
        die("Azure CLI (`az`) not found on PATH. See https://learn.microsoft.com/cli/azure/install-azure-cli")
    account = az("account", "show", check=False)
    if not account or "tenantId" not in account:
        die("Not signed in to Azure CLI. Run `az login` first.")
    tenant_id = account["tenantId"]
    print(f"Signed in to tenant {tenant_id}")

    print("\n[1/4] App registrations…")
    gateway_app = create_or_get_app(APP_NAMES["gateway"])
    frontend_app = create_or_get_app(APP_NAMES["frontend"], redirect_uri=REDIRECT_URI)

    print("\n[2/4] Gateway resource app: identifier URI, v2 tokens, scope…")
    az("ad", "app", "update", "--id", gateway_app["appId"], "--identifier-uris", f"api://{gateway_app['appId']}")
    ensure_v2_access_tokens(gateway_app["id"])
    scope_id = ensure_access_as_user_scope(gateway_app["id"])

    print("\n[3/4] Permissions (sleeping 8s for AAD propagation)…")
    time.sleep(8)
    # Frontend -> resource app: what the BFF requests at sign-in.
    print("  • FrontendApp -> GatewayApp.access_as_user")
    add_permission_and_grant(frontend_app["appId"], gateway_app["appId"], scope_id)
    # Resource app -> itself: the portal's server-side login requests this
    # scope as the resource app, so it needs its own permission consented.
    print("  • GatewayApp -> GatewayApp.access_as_user (portal login client)")
    add_permission_and_grant(gateway_app["appId"], gateway_app["appId"], scope_id)

    print("\n[4/4] Client secrets + .env…")
    new_secrets: dict[str, str] = {}
    if args.rotate_secrets or env_value_is_placeholder("FRONTEND_CLIENT_SECRET"):
        new_secrets["FRONTEND_CLIENT_SECRET"] = reset_client_secret(frontend_app["appId"], "consent-sample-frontend")
    if args.rotate_secrets or env_value_is_placeholder("PORTAL_CLIENT_SECRET"):
        # The portal login client IS the gateway resource app.
        new_secrets["PORTAL_CLIENT_SECRET"] = reset_client_secret(gateway_app["appId"], "consent-sample-portal-login")
    print(f"  ✓ Client secrets: {len(new_secrets)} freshly minted, {2 - len(new_secrets)} kept")

    gid = gateway_app["appId"]
    save_env(
        IDP="entra",
        TENANT_ID=tenant_id,
        GATEWAY_CLIENT_ID=gid,
        PORTAL_CLIENT_ID=gid,
        FRONTEND_CLIENT_ID=frontend_app["appId"],
        IDP_DISCOVERY_URL=f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration",
        # The bare GUID, not api://<GUID>: with v2 tokens Entra sets aud to
        # the application id, and pinning the identifier URI fails validation.
        IDP_AUDIENCE=gid,
        # What the frontend requests at sign-in. Entra's /authorize only
        # accepts the fully qualified form.
        GATEWAY_SCOPE=f"api://{gid}/access_as_user",
        # What the portal requests at its own sign-in (openid is prepended
        # automatically by 02_create_portal.py if you drop it).
        PORTAL_SCOPES=f"openid api://{gid}/access_as_user",
        **new_secrets,
    )
    print("  ✓ Wrote IDs, discovery URL, audience, and scopes to .env")

    print()
    print("✓ Entra ID setup complete (2 apps: gateway resource + frontend).")
    print("  The gateway resource app doubles as the consent portal's login")
    print("  client — see the docstring for why Entra requires that.")
    print()
    print("Next: python deploy/01_create_gateway.py --entra")


if __name__ == "__main__":
    main()
