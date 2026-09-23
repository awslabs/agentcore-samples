"""
Diagnostic: report the current Entra ID configuration for both app
registrations this sample manages. Read-only. Prints:

  1. Service app metadata + keyCredentials + appRoles +
     requiredResourceAccess.
  2. Service app's admin-consent state (appRoleAssignedTo on its SP).
  3. Login web app metadata + keyCredentials + redirectUris.
  4. Login web app's delegated permission grants (oauth2PermissionGrants).

Useful when the automated setup steps look green but a demo still fails -
the printed state is the ground truth of what's actually in Entra.

Run:
    python diagnose_entra_apps.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

MICROSOFT_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"


def _load_helper():
    path = ROOT / "setup" / "_entra_graph.py"
    spec = importlib.util.spec_from_file_location("_entra_graph", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set in .env", file=sys.stderr)
        sys.exit(1)
    return value


def _header(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _bullet(label: str, value) -> None:
    print(f"  {label:<32} = {value}")


def print_service_app(graph, token: str, object_id: str, expected_x5t_s256: str) -> None:
    _header(f"Service app (objectId={object_id})")
    try:
        app = graph.graph_get(
            f"applications/{object_id}",
            token,
            params={
                "$select": (
                    "id,appId,displayName,signInAudience,identifierUris,appRoles,requiredResourceAccess,keyCredentials"
                ),
            },
        )
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  ✗ could not fetch app: {e}")
        return

    _bullet("displayName", app.get("displayName"))
    _bullet("appId", app.get("appId"))
    _bullet("signInAudience", app.get("signInAudience"))
    _bullet("identifierUris", app.get("identifierUris"))

    creds = app.get("keyCredentials") or []
    print(f"  keyCredentials: {len(creds)} entry(ies)")
    matched_cert = False
    for c in creds:
        kid = c.get("customKeyIdentifier")
        # Graph returns customKeyIdentifier as base64. Compare to our
        # X5T_S256_THUMBPRINT (base64url, no padding).
        import base64

        try:
            der_hash = base64.b64decode(kid)
            our_hash = base64.urlsafe_b64decode(expected_x5t_s256 + "==")
            match = der_hash == our_hash
        except (ValueError, TypeError):
            match = False
        marker = "✓ matches X5T_S256_THUMBPRINT" if match else ""
        matched_cert = matched_cert or match
        print(f"    - keyId={c.get('keyId')} type={c.get('type')} usage={c.get('usage')} {marker}")
    if not matched_cert:
        print(
            "    ⚠ no keyCredential matches X5T_S256_THUMBPRINT - the M2M "
            "provider will fail with AADSTS700027 until you re-run setup/02."
        )

    roles = app.get("appRoles") or []
    print(f"  appRoles: {len(roles)} entry(ies)")
    for r in roles:
        print(
            f"    - value={r.get('value')!r} "
            f"allowedMemberTypes={r.get('allowedMemberTypes')} "
            f"isEnabled={r.get('isEnabled')}"
        )

    rra = app.get("requiredResourceAccess") or []
    print(f"  requiredResourceAccess: {len(rra)} entry(ies)")
    for r in rra:
        access = r.get("resourceAccess") or []
        print(
            f"    - resourceAppId={r.get('resourceAppId')} "
            f"({len(access)} permission(s), "
            f"types={sorted({a.get('type') for a in access})})"
        )


def print_service_sp_consent(graph, token: str, sp_object_id: str) -> None:
    _header(f"Service principal appRoleAssignedTo (sp={sp_object_id})")
    try:
        resp = graph.graph_get(
            f"servicePrincipals/{sp_object_id}/appRoleAssignedTo",
            token,
        )
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  ✗ could not fetch appRoleAssignedTo: {e}")
        return
    assignments = resp.get("value") or []
    print(f"  Assignments: {len(assignments)}")
    for a in assignments:
        print(
            f"    - principalId={a.get('principalId')} resourceId={a.get('resourceId')} appRoleId={a.get('appRoleId')}"
        )


def print_login_app(graph, token: str, object_id: str, expected_x5t_s256: str) -> None:
    _header(f"Login web app (objectId={object_id})")
    try:
        app = graph.graph_get(
            f"applications/{object_id}",
            token,
            params={
                "$select": ("id,appId,displayName,signInAudience,web,keyCredentials,requiredResourceAccess"),
            },
        )
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  ✗ could not fetch app: {e}")
        return

    _bullet("displayName", app.get("displayName"))
    _bullet("appId", app.get("appId"))
    _bullet("signInAudience", app.get("signInAudience"))
    web = app.get("web") or {}
    _bullet("web.redirectUris", web.get("redirectUris"))

    creds = app.get("keyCredentials") or []
    print(f"  keyCredentials: {len(creds)} entry(ies)")

    rra = app.get("requiredResourceAccess") or []
    graph_entry = next(
        (r for r in rra if r.get("resourceAppId") == MICROSOFT_GRAPH_APP_ID),
        None,
    )
    if graph_entry:
        access = graph_entry.get("resourceAccess") or []
        print(f"  Microsoft Graph delegated permissions: {len(access)} declared")
    else:
        print("  ⚠ no Microsoft Graph delegated permissions declared")


def print_login_consent(graph, token: str, login_sp_object_id: str, graph_sp_object_id: str) -> None:
    _header("Login web app admin consent (oauth2PermissionGrants)")
    try:
        resp = graph.graph_get(
            "oauth2PermissionGrants",
            token,
            params={
                "$filter": (f"clientId eq '{login_sp_object_id}' and resourceId eq '{graph_sp_object_id}'"),
            },
        )
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  ✗ could not fetch oauth2PermissionGrants: {e}")
        return
    grants = resp.get("value") or []
    if not grants:
        print("  ⚠ no admin-consent grants found (browser 3LO will show a")
        print("    consent screen instead of signing in directly).")
        return
    for g in grants:
        print(f"  - id={g.get('id')}")
        print(f"    consentType={g.get('consentType')}")
        print(f"    scope={g.get('scope')!r}")


def main() -> None:
    load_dotenv(ENV_FILE)
    graph = _load_helper()

    service_obj = must_env("ENTRA_SERVICE_APP_OBJECT_ID")
    service_sp = must_env("ENTRA_SERVICE_SP_OBJECT_ID")
    x5t_s256 = must_env("X5T_S256_THUMBPRINT")

    login_obj = os.environ.get("ENTRA_LOGIN_APP_OBJECT_ID", "")

    tenant_id = must_env("ENTRA_TENANT_ID")

    print(f"Tenant: {tenant_id}")

    print()
    print("→ Acquiring Microsoft Graph access token...")
    token = graph.get_graph_token()
    print("✓ Graph token acquired")

    print_service_app(graph, token, service_obj, x5t_s256)
    print_service_sp_consent(graph, token, service_sp)

    if login_obj:
        print_login_app(graph, token, login_obj, x5t_s256)
        # Find the login web app's SP to query oauth2PermissionGrants.
        login_client_id = os.environ.get("ENTRA_LOGIN_CLIENT_ID", "")
        graph_sp = graph.find_service_principal_by_app_id(MICROSOFT_GRAPH_APP_ID, token)
        login_sp = None
        if login_client_id:
            login_sp = graph.find_service_principal_by_app_id(login_client_id, token)
        if login_sp and graph_sp:
            print_login_consent(graph, token, login_sp["id"], graph_sp["id"])
        else:
            print()
            print("  (Skipping oauth2PermissionGrants - could not resolve SPs.)")
    else:
        print()
        print("• ENTRA_LOGIN_APP_OBJECT_ID not set - skipping login app checks.")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
