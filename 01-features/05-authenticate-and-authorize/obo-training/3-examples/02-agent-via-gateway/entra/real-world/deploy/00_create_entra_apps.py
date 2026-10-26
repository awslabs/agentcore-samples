"""
Automate the Entra ID setup for Use Case 2 (real-world) using the Azure CLI.

What this does (idempotent — safe to re-run):

  1. Creates three Entra app registrations:
       - agentcore-obo-uc2-frontend  (the user-facing OIDC client)
       - agentcore-obo-uc2-agent     (audience for T_user; OBO #1 client)
       - agentcore-obo-uc2-gateway   (audience for T_gateway; OBO #2 client)

  2. Configures:
       - api://<gateway-id>      with scope `access_as_user`
       - api://<agent-id>        with scope `access_as_user`
       - FrontendApp web redirect URI = http://localhost:8000/auth/callback

  3. Adds API permissions (delegated, NOT yet consented):
       - FrontendApp  → AgentApp.access_as_user
       - AgentApp     → GatewayApp.access_as_user
       - GatewayApp   → Microsoft Graph User.Read

  4. Sets knownClientApplications so a single sign-in covers the chain:
       - AgentApp.knownClientApplications   = [FrontendApp.clientId]
       - GatewayApp.knownClientApplications = [AgentApp.clientId]

  5. Grants admin consent on all three apps. (Requires Global Admin or
     Privileged Role Administrator. If the script can't do this, it
     prints clear next steps.)

  6. Creates a client secret per app (only if the corresponding *_CLIENT_SECRET
     in .env is missing or set to "replace-me"). Writes the secret into .env.

  7. Writes every required .env value (TENANT_ID, *_CLIENT_ID, *_CLIENT_SECRET,
     AGENT_SCOPE, GATEWAY_SCOPE).

Prerequisites:
  - Azure CLI (>= 2.50). `brew install azure-cli` or
    https://learn.microsoft.com/en-us/cli/azure/install-azure-cli.
  - You're signed in: `az login` as a user with App Admin (or higher)
    permission in the target tenant.
  - You have permission to grant admin consent. If not, the consent step
    will print next-step instructions.

Run:
    python deploy/00_create_entra_apps.py

Re-running is safe; this script reuses existing apps by display name and only
recreates secrets when the .env value is missing or the placeholder.

Caveats:
  - This script does NOT touch any other tenant configuration (no Conditional
    Access, no group assignments, no Application Proxy, etc.).
  - It uses single-tenant Supported account types (`AzureADMyOrg`). To allow
    other tenants or personal MS accounts, edit the SIGN_IN_AUDIENCE constant.
  - Microsoft Graph application admin permissions can take a few seconds to
    propagate; we sleep 5s before requesting admin consent.
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
from typing import Any


# ── Constants ──────────────────────────────────────────────────────────────
APP_DISPLAY_NAMES = {
    "frontend": "agentcore-obo-uc2-frontend",
    "agent": "agentcore-obo-uc2-agent",
    "gateway": "agentcore-obo-uc2-gateway",
}

REDIRECT_URI = "http://localhost:8000/auth/callback"
SIGN_IN_AUDIENCE = "AzureADMyOrg"

GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
GRAPH_USER_READ_PERMISSION_ID = "e1fe6dd8-ba31-4d61-89e7-88639da4683d"

SCOPE_VALUE = "access_as_user"


# ── Helpers ────────────────────────────────────────────────────────────────
def die(msg: str) -> "type[SystemExit]":
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def az(*args: str, capture: bool = True, check: bool = True) -> Any:
    """Run `az` and return parsed JSON (or None for non-capturing calls)."""
    cmd = ["az", *args]
    if capture and "--output" not in args:
        cmd += ["--output", "json"]
    proc = subprocess.run(cmd, capture_output=capture, text=True)
    if proc.returncode != 0:
        if check:
            die(f"`{' '.join(cmd)}` failed with exit {proc.returncode}:\n{proc.stderr.strip() or proc.stdout.strip()}")
        return None
    if capture and proc.stdout.strip():
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


def find_app_by_display_name(name: str) -> dict | None:
    apps = az("ad", "app", "list", "--display-name", name)
    return apps[0] if apps else None


def create_or_get_app(name: str, *, redirect_uri: str | None = None) -> dict:
    existing = find_app_by_display_name(name)
    if existing:
        print(f"  • App exists: {name} (appId={existing['appId']})")
        # Refresh redirect URI if needed
        if redirect_uri:
            web = existing.get("web", {}) or {}
            uris = web.get("redirectUris", []) or []
            if redirect_uri not in uris:
                az(
                    "ad",
                    "app",
                    "update",
                    "--id",
                    existing["appId"],
                    "--web-redirect-uris",
                    redirect_uri,
                )
                print(f"    ✓ Updated redirect URI: {redirect_uri}")
        return existing

    args = [
        "ad",
        "app",
        "create",
        "--display-name",
        name,
        "--sign-in-audience",
        SIGN_IN_AUDIENCE,
    ]
    if redirect_uri:
        args.extend(["--web-redirect-uris", redirect_uri])
    app = az(*args)
    print(f"  ✓ Created app: {name} (appId={app['appId']})")
    return app


def ensure_identifier_uri(app_id: str, uri: str) -> None:
    """Set api://<appId> as Application ID URI. Idempotent."""
    az("ad", "app", "update", "--id", app_id, "--identifier-uris", uri)


def ensure_v2_access_tokens(object_id: str) -> None:
    """Force the app to issue v2 access tokens.

    Sets api.requestedAccessTokenVersion = 2 in the app manifest. Without this,
    Entra defaults to v1-style tokens (iss = https://sts.windows.net/<tenant>/)
    which don't match AgentCore Gateway's v2.0 OIDC discovery URL. The result
    is a runtime 401 from the Gateway: "Claim 'iss' value mismatch with
    configuration." Setting v2 here means all tokens (T_user, T_gateway, T_graph)
    are consistent — v2-style with iss = https://login.microsoftonline.com/
    <tenant>/v2.0.

    Idempotent: no-op if already set to 2.
    """
    app = graph_get(f"/applications/{object_id}")
    current = (app.get("api") or {}).get("requestedAccessTokenVersion")
    if current == 2:
        print("    • api.requestedAccessTokenVersion already 2")
        return
    graph_patch(f"/applications/{object_id}", {"api": {"requestedAccessTokenVersion": 2}})
    print(f"    ✓ Set api.requestedAccessTokenVersion = 2 (was {current!r})")


def ensure_access_as_user_scope(object_id: str) -> str:
    """Create the `access_as_user` Expose-an-API scope if absent.

    Returns the scope's UUID (used by callers to add API permissions).
    """
    app = graph_get(f"/applications/{object_id}")
    api = app.get("api") or {}
    scopes: list[dict] = list(api.get("oauth2PermissionScopes") or [])

    for s in scopes:
        if s.get("value") == SCOPE_VALUE:
            # Print just the scope value; the id is not user-facing and
            # descending into the Microsoft Graph response dict triggers
            # CodeQL's taint tracker even though scope IDs aren't secret.
            print(f"    • Scope already exists: {SCOPE_VALUE}")
            return s["id"]

    scope_id = str(uuid.uuid4())
    new_scope = {
        "adminConsentDescription": ("Allows the calling application to invoke this API as the signed-in user."),
        "adminConsentDisplayName": "Access as the signed-in user",
        "id": scope_id,
        "isEnabled": True,
        "type": "User",
        "userConsentDescription": None,
        "userConsentDisplayName": None,
        "value": SCOPE_VALUE,
    }
    scopes.append(new_scope)
    graph_patch(f"/applications/{object_id}", {"api": {"oauth2PermissionScopes": scopes}})
    print(f"    ✓ Added scope: {SCOPE_VALUE} (id={scope_id})")
    return scope_id


def add_api_permission(consumer_app_id: str, *, api_app_id: str, permission_id: str, label: str = "") -> None:
    """Add a delegated API permission to a consumer app. Idempotent (az dedupes)."""
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
        f"{permission_id}=Scope",
        # az emits a misleading warning here; suppress it by capturing.
        check=False,
    )
    print(f"    ✓ Added permission to {consumer_app_id}: {label}")


def set_known_client_apps(object_id: str, known_app_ids: list[str]) -> None:
    """Set knownClientApplications on the app whose objectId is given."""
    graph_patch(
        f"/applications/{object_id}",
        {"api": {"knownClientApplications": known_app_ids}},
    )
    print(f"    ✓ Set knownClientApplications: {known_app_ids}")


def grant_admin_consent(app_id: str, label: str) -> bool:
    """Grant admin consent for all permissions on the app.

    Returns True on success. On insufficient permissions returns False (caller
    surfaces a clear next-steps message).
    """
    proc = subprocess.run(
        ["az", "ad", "app", "permission", "admin-consent", "--id", app_id],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        print(f"    ✓ Granted admin consent: {label}")
        return True
    err = (proc.stderr or proc.stdout).strip()
    print(f"    ⚠ admin-consent failed for {label}: {err.splitlines()[-1] if err else 'unknown'}")
    return False


def reset_client_secret(app_id: str, *, display_name: str) -> str:
    """Always creates and returns a NEW secret, appending to existing creds."""
    result = az(
        "ad",
        "app",
        "credential",
        "reset",
        "--id",
        app_id,
        "--display-name",
        display_name,
        "--years",
        "1",
        "--append",
    )
    return result["password"]


def upsert_env_value(env_path: Path, key: str, value: str) -> None:
    """Insert/replace `KEY=value` in .env, preserving everything else."""
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n")
        return
    lines = env_path.read_text().splitlines()
    prefix = f"{key}="
    found = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


def env_value_is_placeholder(env_path: Path, key: str) -> bool:
    """True if .env doesn't have KEY, or sets it to empty / 'replace-me'."""
    if not env_path.exists():
        return True
    for line in env_path.read_text().splitlines():
        if line.startswith(f"{key}="):
            value = line[len(key) + 1 :].strip()
            return value in ("", "replace-me", "REPLACE_ME")
    return True


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate-secrets",
        action="store_true",
        help="Force rotation of client secrets even if .env already has values.",
    )
    args = parser.parse_args()

    if not shutil.which("az"):
        die(
            "Azure CLI (`az`) not found on PATH. Install it first:\n"
            "  https://learn.microsoft.com/en-us/cli/azure/install-azure-cli"
        )

    # Verify signed in.
    account = az("account", "show", check=False)
    if not account or "tenantId" not in account:
        die("Not signed in to Azure CLI. Run `az login` first.")
    tenant_id = account["tenantId"]
    user_name = account.get("user", {}).get("name", "(unknown)")
    print(f"Signed in as {user_name}, tenant {tenant_id}")

    real_world_root = Path(__file__).resolve().parent.parent
    env_path = real_world_root / ".env"
    if not env_path.exists():
        # Bootstrap from the example file if needed.
        example = real_world_root / "config.example.env"
        if example.exists():
            env_path.write_text(example.read_text())
            print(f"  • Bootstrapped {env_path.name} from config.example.env")
        else:
            env_path.write_text("")

    # 1) Create or fetch the three apps.
    print("\n[1/7] Creating app registrations…")
    gateway_app = create_or_get_app(APP_DISPLAY_NAMES["gateway"])
    agent_app = create_or_get_app(APP_DISPLAY_NAMES["agent"])
    frontend_app = create_or_get_app(
        APP_DISPLAY_NAMES["frontend"],
        redirect_uri=REDIRECT_URI,
    )

    # 2) Identifier URIs + v2 access tokens + Expose-an-API scopes on Agent
    #    and Gateway apps.
    print("\n[2/7] Configuring identifier URIs, v2 access tokens, and scopes…")
    print(f"  • {APP_DISPLAY_NAMES['gateway']}")
    ensure_identifier_uri(gateway_app["appId"], f"api://{gateway_app['appId']}")
    ensure_v2_access_tokens(gateway_app["id"])
    gateway_scope_id = ensure_access_as_user_scope(gateway_app["id"])
    print(f"  • {APP_DISPLAY_NAMES['agent']}")
    ensure_identifier_uri(agent_app["appId"], f"api://{agent_app['appId']}")
    ensure_v2_access_tokens(agent_app["id"])
    agent_scope_id = ensure_access_as_user_scope(agent_app["id"])

    # 3) API permissions (delegated). Not consented yet.
    print("\n[3/7] Adding API permissions (delegated)…")
    print(f"  • FrontendApp → AgentApp.{SCOPE_VALUE}")
    add_api_permission(
        frontend_app["appId"],
        api_app_id=agent_app["appId"],
        permission_id=agent_scope_id,
        label=f"AgentApp.{SCOPE_VALUE}",
    )
    print(f"  • AgentApp → GatewayApp.{SCOPE_VALUE}")
    add_api_permission(
        agent_app["appId"],
        api_app_id=gateway_app["appId"],
        permission_id=gateway_scope_id,
        label=f"GatewayApp.{SCOPE_VALUE}",
    )
    print("  • GatewayApp → Microsoft Graph User.Read")
    add_api_permission(
        gateway_app["appId"],
        api_app_id=GRAPH_APP_ID,
        permission_id=GRAPH_USER_READ_PERMISSION_ID,
        label="Graph User.Read",
    )

    # 4) knownClientApplications — combined consent chain.
    print("\n[4/7] Setting knownClientApplications…")
    print("  • AgentApp lists FrontendApp")
    set_known_client_apps(agent_app["id"], [frontend_app["appId"]])
    print("  • GatewayApp lists AgentApp")
    set_known_client_apps(gateway_app["id"], [agent_app["appId"]])

    # 5) Admin consent. Sleep first because the just-added permissions can
    #    take a few seconds to be visible to the consent endpoint.
    print("\n[5/7] Granting admin consent (sleeping 8s for AAD propagation)…")
    time.sleep(8)
    consents = {
        "FrontendApp": grant_admin_consent(frontend_app["appId"], "FrontendApp"),
        "AgentApp": grant_admin_consent(agent_app["appId"], "AgentApp"),
        "GatewayApp": grant_admin_consent(gateway_app["appId"], "GatewayApp"),
    }
    if not all(consents.values()):
        print()
        print("⚠ Some admin-consent calls failed. The most likely cause is that")
        print("  your account doesn't have a role that can grant tenant-wide consent.")
        print("  Ask a Global Admin / Privileged Role Administrator to:")
        for app_label, ok in consents.items():
            if ok:
                continue
            app_obj = {
                "FrontendApp": frontend_app,
                "AgentApp": agent_app,
                "GatewayApp": gateway_app,
            }[app_label]
            print(
                f"    - Open Entra → App registrations → {app_obj['displayName']} "
                f"({app_obj['appId']}) → API permissions → "
                f'"Grant admin consent for <tenant>".'
            )
        print("  Or have them run: az ad app permission admin-consent --id <appId>")

    # 6) Client secrets — only mint new ones if .env has a placeholder
    #    (or --rotate-secrets is given).
    print("\n[6/7] Creating client secrets where needed…")

    secret_map = {
        "FRONTEND_CLIENT_SECRET": (frontend_app, APP_DISPLAY_NAMES["frontend"]),
        "AGENT_CLIENT_SECRET": (agent_app, APP_DISPLAY_NAMES["agent"]),
        "GATEWAY_CLIENT_SECRET": (gateway_app, APP_DISPLAY_NAMES["gateway"]),
    }
    # Mint or keep client secrets. We intentionally do NOT log per-app
    # progress here — CodeQL's clear-text-logging query flags any print
    # inside a scope where a client_secret variable exists, even for
    # messages that only reference the app label. The summary count
    # printed by the caller after this block is sufficient.
    new_secrets: dict[str, str] = {}
    for env_key, (app_obj, name) in secret_map.items():
        if args.rotate_secrets or env_value_is_placeholder(env_path, env_key):
            new_secrets[env_key] = reset_client_secret(app_obj["appId"], display_name=f"deploy-{name}")
    rotated = len(new_secrets)
    kept = len(secret_map) - rotated
    print(f"  ✓ Client secrets: {rotated} freshly minted, {kept} kept (use --rotate-secrets to force-rotate all)")

    # 7) Persist values to .env.
    print("\n[7/7] Writing .env…")
    env_writes = {
        "TENANT_ID": tenant_id,
        "FRONTEND_CLIENT_ID": frontend_app["appId"],
        "AGENT_CLIENT_ID": agent_app["appId"],
        "GATEWAY_CLIENT_ID": gateway_app["appId"],
        "AGENT_SCOPE": f"api://{agent_app['appId']}/access_as_user",
        "GATEWAY_SCOPE": f"api://{gateway_app['appId']}/access_as_user",
    }
    env_writes.update(new_secrets)
    # Silently persist to .env. We deliberately do NOT print the keys or
    # values here — every element of `env_writes` is derived from a
    # Microsoft Graph API response (or a freshly minted secret), and
    # CodeQL's taint tracker flags any print that reads from that dict,
    # even for values it labels as ***. The keys are named deterministically
    # per config.example.env, and the user can inspect .env directly.
    for key, value in env_writes.items():
        upsert_env_value(env_path, key, value)
    print(f"  ✓ Wrote {len(env_writes)} value(s) to .env (tenant/client IDs and any freshly-minted secrets).")

    print()
    print("✓ Entra ID setup complete.")
    print()
    print("Verify (tenant + client IDs only; secrets stay in .env):")
    print(
        "  cat .env | grep -E '^(TENANT_ID|FRONTEND_CLIENT_ID|AGENT_CLIENT_ID|GATEWAY_CLIENT_ID|AGENT_SCOPE|GATEWAY_SCOPE)='"
    )
    print()
    print("Next step: python deploy/01_create_providers.py")


if __name__ == "__main__":
    main()
