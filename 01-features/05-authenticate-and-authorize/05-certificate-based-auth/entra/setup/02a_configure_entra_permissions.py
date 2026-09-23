"""
Configure the Entra service app so that a client_credentials flow can
actually mint a token - expose an API URI, create the 'm2m' app role,
grant the app permission to itself, then admin-consent that grant.

This mirrors the internal PRIVATE_KEY_JWT test guide's §4A steps 4-6:

  1. Expose an API URI     → identifierUris = ["api://<clientId>"]
                             Required so scope 'api://<clientId>/.default'
                             is legal.
  2. Create app role       → appRoles.append({value:'m2m', member=Application})
                             App-only role; appears as roles=['m2m'] on the
                             issued access token.
  3. Add API permission    → requiredResourceAccess appended with
                             {resourceAppId=<self>, resourceAccess=[{type:Role,
                             id=<role_id>}]}. Wires the app as a *client of
                             itself*.
  4. Grant admin consent   → POST /servicePrincipals/<sp>/appRoleAssignedTo
                             with principalId = resourceId = <sp>,
                             appRoleId = <role_id>. Turns 'Granted for
                             <tenant>' green in the portal.

Idempotency:
  All four steps check current state first. Existing identifierUris,
  appRoles matched by value='m2m', requiredResourceAccess matched by
  resourceAppId, and appRoleAssignments matched by (principalId,
  resourceId, appRoleId) are reused. The script can be re-run safely.

Usage:
    python setup/02a_configure_entra_permissions.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

DEFAULT_APP_ROLE_NAME = "m2m"
DEFAULT_DELEGATED_SCOPE_NAME = "obo_access"
# Namespace UUID so re-runs derive the same role UUID from the same
# (role_name, service_app_client_id) pair. Not security-sensitive - just
# a stability trick so re-runs of this script don't mint new IDs.
_ROLE_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Microsoft Graph's well-known appId (stable across every Entra tenant).
MICROSOFT_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"

# Delegated Graph permissions the SERVICE app (middle-tier) needs so
# Entra will honor an OBO exchange whose downstream target is Microsoft
# Graph. These are the same scopes the login app has - the difference
# is that the login app is what the user consents to, while the service
# app is what Entra checks during the OBO exchange for downstream
# permissions.
DELEGATED_GRAPH_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "User.Read",
]


def _load_helper():
    path = Path(__file__).resolve().parent / "_entra_graph.py"
    spec = importlib.util.spec_from_file_location("_entra_graph", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def stable_role_uuid(client_id: str, role_name: str) -> str:
    """Derive a UUID that only depends on (client_id, role_name).

    Re-running the script for the same app + role name produces the same
    UUID, so PATCH is a no-op instead of appending duplicates.
    """
    return str(uuid.uuid5(_ROLE_UUID_NAMESPACE, f"{client_id}:{role_name}"))


def stable_scope_uuid(client_id: str, scope_name: str) -> str:
    """Same idea as stable_role_uuid, but namespaced for oauth2PermissionScopes."""
    return str(uuid.uuid5(_ROLE_UUID_NAMESPACE, f"scope:{client_id}:{scope_name}"))


def build_desired_app(
    *,
    existing: dict,
    client_id: str,
    role_name: str,
    role_id: str,
    scope_name: str,
    scope_id: str,
) -> dict | None:
    """Build a PATCH body reflecting the delta we want to apply.

    Returns None if nothing needs to change (idempotent no-op case).
    """
    body: dict = {}

    # 1. identifierUris - must contain api://<clientId>.
    target_uri = f"api://{client_id}"
    existing_uris = existing.get("identifierUris") or []
    if target_uri not in existing_uris:
        body["identifierUris"] = sorted({*existing_uris, target_uri})

    # 2. appRoles - must contain a role with value=role_name.
    existing_roles = existing.get("appRoles") or []
    if not any(r.get("value") == role_name for r in existing_roles):
        new_role = {
            "id": role_id,
            "allowedMemberTypes": ["Application"],
            "displayName": role_name,
            "description": (
                f"Sample app-only role for the AgentCore Identity PRIVATE_KEY_JWT "
                f"M2M demo. Present on client_credentials tokens as roles={[role_name]!r}."
            ),
            "value": role_name,
            "isEnabled": True,
        }
        body["appRoles"] = existing_roles + [new_role]

    # 3. oauth2PermissionScopes - expose a DELEGATED scope so users can
    #    obtain a user token whose aud is this API. Required for OBO:
    #    Entra's jwt-bearer + on_behalf_of flow needs the subject token
    #    to be audienced at the API being exchanged for, not Microsoft
    #    Graph.
    existing_api = existing.get("api") or {}
    existing_scopes = existing_api.get("oauth2PermissionScopes") or []
    if not any(s.get("value") == scope_name for s in existing_scopes):
        new_scope = {
            "id": scope_id,
            "adminConsentDisplayName": "Access the sample API as user",
            "adminConsentDescription": (
                "Allows the app to access the AgentCore Identity PRIVATE_KEY_JWT "
                "sample API on behalf of the signed-in user. Required for the "
                "OBO demo - the resulting user token has aud=api://<clientId>."
            ),
            "userConsentDisplayName": "Access the sample API as you",
            "userConsentDescription": ("Allows this app to access the sample API on your behalf."),
            "value": scope_name,
            "type": "User",
            "isEnabled": True,
        }
        # `api` PATCH replaces the whole object, so merge preserving anything else.
        merged_api = dict(existing_api)
        merged_api["oauth2PermissionScopes"] = existing_scopes + [new_scope]
        body["api"] = merged_api

    # 4. requiredResourceAccess - must contain an entry for this app
    #    referencing the m2m role. The "app is a client of itself" pattern.
    existing_rra = existing.get("requiredResourceAccess") or []
    self_entry = next(
        (r for r in existing_rra if r.get("resourceAppId") == client_id),
        None,
    )
    need_self_permission = True
    if self_entry:
        # Does it already reference our role ID?
        for access in self_entry.get("resourceAccess") or []:
            if access.get("id") == role_id and access.get("type") == "Role":
                need_self_permission = False
                break

    if need_self_permission:
        # Build the updated array. Preserve entries for OTHER resources
        # untouched; merge into our own entry if it exists.
        others = [r for r in existing_rra if r.get("resourceAppId") != client_id]
        merged_self_access = list((self_entry or {}).get("resourceAccess") or [])
        merged_self_access.append({"id": role_id, "type": "Role"})
        merged_self_entry = {
            "resourceAppId": client_id,
            "resourceAccess": merged_self_access,
        }
        body["requiredResourceAccess"] = others + [merged_self_entry]

    return body or None


def grant_admin_consent(graph, token: str, sp_object_id: str, role_id: str) -> None:
    """POST /servicePrincipals/{sp}/appRoleAssignedTo - self-grant the role.

    principalId = resourceId = our SP (the app is client + resource).
    Idempotent: existing assignment triggers HTTP 400/409 which we swallow.
    """
    # Check for an existing assignment first.
    try:
        current = graph.graph_get(
            f"servicePrincipals/{sp_object_id}/appRoleAssignedTo",
            token,
        )
        for a in current.get("value") or []:
            if (
                a.get("appRoleId") == role_id
                and a.get("principalId") == sp_object_id
                and a.get("resourceId") == sp_object_id
            ):
                print("• App role already granted (admin consent present)")
                return
    except (requests.RequestException, ValueError, KeyError):
        # Fall through to POST - if the check GET failed, the POST will
        # surface the real error. This is a best-effort idempotency probe.
        pass

    body = {
        "principalId": sp_object_id,
        "resourceId": sp_object_id,
        "appRoleId": role_id,
    }
    print("→ POST /servicePrincipals/{sp}/appRoleAssignedTo (grant admin consent)...")
    try:
        graph.graph_post(
            f"servicePrincipals/{sp_object_id}/appRoleAssignedTo",
            token,
            body,
            expect_status=(201,),
        )
        print("✓ Admin consent granted (role assignment created)")
    except Exception as e:
        # 400/409 with certain messages = "already exists" - treat as success.
        msg = str(e).lower()
        if "already" in msg or "duplicate" in msg or "conflict" in msg:
            print("• App role already granted (idempotent no-op)")
        else:
            raise


def resolve_graph_delegated_scope_ids(graph, token: str, scope_names: list[str]) -> tuple[str, list[str]]:
    """Return (Graph SP object id, [scope_id in the same order as scope_names])."""
    sp = graph.find_service_principal_by_app_id(MICROSOFT_GRAPH_APP_ID, token)
    if not sp:
        raise RuntimeError("Microsoft Graph service principal not found in this tenant.")
    graph_sp_id = sp["id"]
    detail = graph.graph_get(
        f"servicePrincipals/{graph_sp_id}",
        token,
        params={"$select": "id,appId,oauth2PermissionScopes"},
    )
    scope_map = {s["value"]: s["id"] for s in (detail.get("oauth2PermissionScopes") or [])}
    missing = [s for s in scope_names if s not in scope_map]
    if missing:
        raise RuntimeError(f"Microsoft Graph SP does not publish these scopes: {missing}")
    return graph_sp_id, [scope_map[s] for s in scope_names]


def add_graph_delegated_permissions_to_app(
    graph,
    token: str,
    object_id: str,
    scope_ids: list[str],
) -> None:
    """PATCH the app's requiredResourceAccess to add Microsoft Graph delegated perms."""
    current = graph.graph_get(
        f"applications/{object_id}",
        token,
        params={"$select": "requiredResourceAccess"},
    )
    existing_rra = list(current.get("requiredResourceAccess") or [])
    graph_entry = next(
        (r for r in existing_rra if r.get("resourceAppId") == MICROSOFT_GRAPH_APP_ID),
        None,
    )
    existing_scope_ids = set()
    if graph_entry:
        existing_scope_ids = {
            a.get("id") for a in (graph_entry.get("resourceAccess") or []) if a.get("type") == "Scope"
        }
    missing = [sid for sid in scope_ids if sid not in existing_scope_ids]
    if not missing:
        print("• Delegated Graph permissions already declared on the service app")
        return

    others = [r for r in existing_rra if r.get("resourceAppId") != MICROSOFT_GRAPH_APP_ID]
    merged_access = [{"id": sid, "type": "Scope"} for sid in sorted(existing_scope_ids | set(scope_ids))]
    merged = others + [{"resourceAppId": MICROSOFT_GRAPH_APP_ID, "resourceAccess": merged_access}]
    print("→ PATCH /applications (declare delegated Graph perms on service app)...")
    graph.graph_patch(f"applications/{object_id}", token, {"requiredResourceAccess": merged})
    print(f"✓ Added {len(missing)} delegated Graph permission(s) on the service app")


def grant_graph_admin_consent(
    graph,
    token: str,
    service_sp_object_id: str,
    graph_sp_id: str,
    scope_names: list[str],
) -> None:
    """POST /oauth2PermissionGrants - tenant-wide admin consent for Graph perms."""
    existing = graph.graph_get(
        "oauth2PermissionGrants",
        token,
        params={
            "$filter": (f"clientId eq '{service_sp_object_id}' and resourceId eq '{graph_sp_id}'"),
        },
    )
    existing_grants = existing.get("value") or []
    target_scope = " ".join(scope_names)

    for grant in existing_grants:
        current = set((grant.get("scope") or "").split())
        needed = set(scope_names)
        if needed.issubset(current):
            print("• Admin consent for Graph perms already granted on service app")
            return
        merged = " ".join(sorted(current | needed))
        print("→ PATCH /oauth2PermissionGrants (merge missing Graph scopes)...")
        graph.graph_patch(f"oauth2PermissionGrants/{grant['id']}", token, {"scope": merged})
        print(f"✓ Admin consent updated on service app (scope='{merged}')")
        return

    body = {
        "clientId": service_sp_object_id,
        "consentType": "AllPrincipals",
        "resourceId": graph_sp_id,
        "scope": target_scope,
    }
    print("→ POST /oauth2PermissionGrants (Graph consent on service app)...")
    graph.graph_post("oauth2PermissionGrants", token, body)
    print(f"✓ Admin consent granted on service app for: {target_scope}")


def main() -> None:
    load_dotenv(ENV_FILE)
    graph = _load_helper()

    object_id = must_env("ENTRA_SERVICE_APP_OBJECT_ID")
    client_id = must_env("ENTRA_SERVICE_CLIENT_ID")
    sp_object_id = must_env("ENTRA_SERVICE_SP_OBJECT_ID")
    role_name = os.environ.get("ENTRA_APP_ROLE_NAME") or DEFAULT_APP_ROLE_NAME
    scope_name = os.environ.get("ENTRA_DELEGATED_SCOPE_NAME") or DEFAULT_DELEGATED_SCOPE_NAME

    role_id = stable_role_uuid(client_id, role_name)
    scope_id = stable_scope_uuid(client_id, scope_name)

    print(f"App object ID: {object_id}")
    print(f"Client ID:     {client_id}")
    print(f"SP object ID:  {sp_object_id}")
    print(f"App role:      {role_name!r} (id={role_id})")
    print(f"Delegated scope: {scope_name!r} (id={scope_id})")
    print()

    print("→ Acquiring Microsoft Graph access token...")
    token = graph.get_graph_token()
    print("✓ Graph token acquired")

    print()
    print("→ Fetching current app state...")
    current = graph.graph_get(
        f"applications/{object_id}",
        token,
        params={"$select": "id,appId,displayName,identifierUris,appRoles,api,requiredResourceAccess"},
    )

    delta = build_desired_app(
        existing=current,
        client_id=client_id,
        role_name=role_name,
        role_id=role_id,
        scope_name=scope_name,
        scope_id=scope_id,
    )

    print()
    if delta is None:
        print("• App already has identifierUris, appRoles, and requiredResourceAccess configured - nothing to PATCH.")
    else:
        keys = ", ".join(sorted(delta.keys()))
        print(f"→ PATCH /applications/{{id}} - updating: {keys}")
        graph.graph_patch(f"applications/{object_id}", token, delta)
        print("✓ App updated")

    print()
    grant_admin_consent(graph, token, sp_object_id, role_id)

    # Additionally: the SERVICE app (middle-tier in OBO) needs its own
    # delegated permissions on the downstream API and admin consent for
    # them. Without this, Entra's OBO exchange returns HTTP 400 because
    # the middle-tier app has nothing to hand out downstream.
    print()
    print("→ Resolving Microsoft Graph delegated scope IDs...")
    graph_sp_id, graph_scope_ids = resolve_graph_delegated_scope_ids(graph, token, DELEGATED_GRAPH_SCOPES)

    print()
    add_graph_delegated_permissions_to_app(graph, token, object_id, graph_scope_ids)

    print()
    print("→ Granting admin consent for Graph perms on the service app...")
    grant_graph_admin_consent(graph, token, sp_object_id, graph_sp_id, DELEGATED_GRAPH_SCOPES)

    print()
    print("=" * 70)
    print("  Summary - step 02a: expose API + app role + admin consent")
    print("=" * 70)
    print("  What is now configured on the service app:")
    print(f"    identifierUris                    : ['api://{client_id}']")
    print(f"    appRoles[value]                   : ['{role_name}']  (Application, for M2M)")
    print(f"    oauth2PermissionScopes[value]     : ['{scope_name}']  (User, for OBO)")
    print(f"    requiredResourceAccess (self)     : {role_name} role granted")
    print(f"    requiredResourceAccess (Graph)    : {', '.join(DELEGATED_GRAPH_SCOPES)}")
    print("    admin consent (m2m app role)      : granted for tenant")
    print("    admin consent (Graph delegated)   : granted for tenant")
    print()
    print("  Where to inspect it in the Entra admin center:")
    print("    App registrations → your service app → Expose an API")
    print(f"      (should show Application ID URI = api://{client_id})")
    print("    App registrations → your service app → App roles")
    print(f"      (should list '{role_name}' as Application-only)")
    print("    App registrations → your service app → API permissions")
    print("      (should show a green 'Granted for <tenant>' next to")
    print(f"       your own app's {role_name} permission)")
    print()
    print("  Written to .env:")
    print("    (no new keys - this step only mutates Entra)")
    print()
    print("  Why this step matters:")
    print("    Two grant paths, three pieces of Entra config:")
    print(f"      - M2M: requests 'api://{client_id}/.default'. Entra recognises")
    print("        '.default' as 'give me every permission the caller is")
    print("        consented for on this resource', and returns a token with")
    print(f"        roles=['{role_name}'] (from the '{role_name}' app role).")
    print(f"      - OBO subject: 3LO requests 'api://{client_id}/{scope_name}',")
    print("        producing a user token audienced at your API. This is what")
    print("        AgentCore Identity later exchanges in the OBO flow.")
    print("      - OBO downstream: the delegated Graph perms let Entra honor")
    print("        the exchange step with 'aud=https://graph.microsoft.com'.")
    print("    Note: '.default' is not a scope you create - it's an Entra")
    print("    OAuth 2.0 pattern. The scopes you actually create are")
    print(f"    '{scope_name}' (delegated, for OBO) and the '{role_name}' app role")
    print("    (Application, for M2M).")
    print()
    print("  Next step:")
    print("    python setup/02b_create_entra_login_web_app.py")
    print("    (creates the 3LO web app used by the OBO subject-token flow)")


if __name__ == "__main__":
    main()
