"""
Create the Entra ID web-platform app used for user 3LO sign-in during the
OBO demo. Same KMS-signed certificate as the service app (setup/02) -
different appId, different grant types, different permissions.

Why a second app:
  The service app (setup/02) is single-purpose: it authenticates as a
  confidential client to Entra's /token endpoint for M2M and OBO. The
  authorization_code grant used during browser 3LO needs a redirect URI
  and delegated user permissions on Microsoft Graph - different concerns.
  Splitting them keeps each app's audit trail clean and matches Microsoft's
  official Entra samples.

What this script does:
  1. Reads the KMS-signed cert (from setup/01) and the service app's cert
     thumbprint from .env.
  2. Acquires a Microsoft Graph token via _entra_graph.get_graph_token.
  3. Finds an existing login-web-app registration by displayName, or
     creates one via POST /applications with:
       - signInAudience: AzureADMyOrg
       - web.redirectUris: [placeholder] - replaced by setup/04b with
         AgentCore Identity's managed callback URL.
  4. PATCHes the app's keyCredentials to include the same certificate
     from setup/01 (idempotent on customKeyIdentifier).
  5. PATCHes the app's requiredResourceAccess to declare delegated
     Microsoft Graph permissions:
         openid, profile, email, offline_access, User.Read
  6. Ensures a service principal exists for the app.
  7. Grants admin consent for the delegated permissions tenant-wide via
     POST /oauth2PermissionGrants (consentType=AllPrincipals). Idempotent.
  8. Writes ENTRA_LOGIN_APP_OBJECT_ID and ENTRA_LOGIN_CLIENT_ID to .env.

Idempotent: re-runs are safe. All PATCHes preserve existing state.

Usage:
    python setup/02b_create_entra_login_web_app.py
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
CERT_DER_PATH = ROOT / "entra_cert.der"

DEFAULT_LOGIN_APP_LABEL = "AgentCore Identity Private Key JWT Login App"
DEFAULT_DELEGATED_SCOPE_NAME = "obo_access"

# Microsoft Graph's well-known appId - the same across every Entra tenant.
MICROSOFT_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"

# The delegated permissions we want to grant to the login web app. These
# names are what the user consents to; the setup script looks them up on
# the Microsoft Graph service principal to translate names → GUIDs.
DELEGATED_GRAPH_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "User.Read",
]

# Placeholder Entra requires at creation time. setup/04b will replace
# this with AgentCore Identity's actual managed callback URL.
PLACEHOLDER_REDIRECT_URI = "https://placeholder.example.com/set-by-setup-04b"


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


def write_env_var(name: str, value: str) -> None:
    text = ENV_FILE.read_text()
    pattern = rf"^{re.escape(name)}=.*$"
    replacement = f"{name}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        new_text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(new_text)
    print(f"✓ Wrote {name} to .env")


def build_key_credential(cert_der: bytes) -> dict:
    encoded = base64.b64encode(cert_der).decode("ascii")
    key_id = base64.b64encode(hashlib.sha256(cert_der).digest()).decode("ascii")
    return {
        "customKeyIdentifier": key_id,
        "type": "AsymmetricX509Cert",
        "usage": "Verify",
        "displayName": "AgentCore Identity PRIVATE_KEY_JWT (login app)",
        "key": encoded,
    }


def create_web_app(graph, token: str, label: str) -> dict:
    """POST /applications for a single-tenant web app with a placeholder redirect."""
    body = {
        "displayName": label,
        "signInAudience": "AzureADMyOrg",
        "web": {
            "redirectUris": [PLACEHOLDER_REDIRECT_URI],
            "implicitGrantSettings": {
                "enableIdTokenIssuance": False,
                "enableAccessTokenIssuance": False,
            },
        },
    }
    print(f"→ POST /applications (create '{label}')...")
    resp = graph.graph_post("applications", token, body)
    print(f"✓ Created web app (appId={resp.get('appId')})")
    return resp


def upsert_key_credential(graph, token: str, object_id: str, existing_creds: list, cert_der: bytes) -> None:
    target = build_key_credential(cert_der)
    target_id = target["customKeyIdentifier"]
    if any((c.get("customKeyIdentifier") == target_id) for c in existing_creds):
        print("• Certificate already present in login app's keyCredentials")
        return
    sanitized = [{k: v for k, v in c.items() if v is not None and k != "keyId"} for c in existing_creds]
    updated = sanitized + [target]
    print("→ PATCH /applications (add certificate to keyCredentials)...")
    graph.graph_patch(f"applications/{object_id}", token, {"keyCredentials": updated})
    print("✓ Certificate added to keyCredentials")


def get_graph_scope_id_map(graph, token: str) -> tuple[str, dict[str, str]]:
    """Return (Graph SP object id, {scope_value: scope_id}) for Microsoft Graph."""
    sp = graph.find_service_principal_by_app_id(MICROSOFT_GRAPH_APP_ID, token)
    if not sp:
        print(
            "✗ Microsoft Graph service principal not found in this tenant. "
            "That should be impossible - did az login succeed against the right tenant?",
            file=sys.stderr,
        )
        sys.exit(1)
    graph_sp_id = sp["id"]

    detail = graph.graph_get(
        f"servicePrincipals/{graph_sp_id}",
        token,
        params={"$select": "id,appId,oauth2PermissionScopes"},
    )
    scope_map = {s["value"]: s["id"] for s in (detail.get("oauth2PermissionScopes") or [])}
    return graph_sp_id, scope_map


def build_required_resource_access(scope_ids: list[str], graph_app_id: str) -> list[dict]:
    """Build a requiredResourceAccess entry targeting Microsoft Graph."""
    return [
        {
            "resourceAppId": graph_app_id,
            "resourceAccess": [{"id": sid, "type": "Scope"} for sid in scope_ids],
        }
    ]


def upsert_required_resource_access(
    graph,
    token: str,
    object_id: str,
    existing: list,
    graph_app_id: str,
    scope_ids: list[str],
) -> None:
    """Merge a Microsoft Graph entry into the app's requiredResourceAccess."""
    existing_graph_entry = next(
        (r for r in (existing or []) if r.get("resourceAppId") == graph_app_id),
        None,
    )
    existing_scope_ids = set()
    if existing_graph_entry:
        existing_scope_ids = {
            a.get("id") for a in (existing_graph_entry.get("resourceAccess") or []) if a.get("type") == "Scope"
        }
    missing = [sid for sid in scope_ids if sid not in existing_scope_ids]
    if not missing:
        print("• Delegated Graph permissions already declared on the app")
        return

    others = [r for r in (existing or []) if r.get("resourceAppId") != graph_app_id]
    merged_access = [{"id": sid, "type": "Scope"} for sid in sorted(existing_scope_ids | set(scope_ids))]
    merged = others + [{"resourceAppId": graph_app_id, "resourceAccess": merged_access}]

    print("→ PATCH /applications (declare delegated Graph permissions)...")
    graph.graph_patch(f"applications/{object_id}", token, {"requiredResourceAccess": merged})
    print(f"✓ Added {len(missing)} delegated permission(s)")


def grant_admin_consent_delegated(
    graph,
    token: str,
    login_sp_id: str,
    graph_sp_id: str,
    scope_names: list[str],
) -> None:
    """POST /oauth2PermissionGrants - admin-consent all requested delegated scopes.

    consentType=AllPrincipals grants the scopes tenant-wide (admin
    consent). Idempotent: if a grant already exists between the same
    clientId and resourceId, we merge scopes into it.
    """
    existing = graph.graph_get(
        "oauth2PermissionGrants",
        token,
        params={
            "$filter": (f"clientId eq '{login_sp_id}' and resourceId eq '{graph_sp_id}'"),
        },
    )
    existing_grants = existing.get("value") or []
    target_scope = " ".join(scope_names)

    for grant in existing_grants:
        current_scopes = set((grant.get("scope") or "").split())
        needed_scopes = set(scope_names)
        if needed_scopes.issubset(current_scopes):
            print("• Admin consent already granted for all delegated permissions")
            return
        # Merge missing scopes into existing grant.
        merged = " ".join(sorted(current_scopes | needed_scopes))
        print("→ PATCH /oauth2PermissionGrants (merge missing scopes)...")
        graph.graph_patch(f"oauth2PermissionGrants/{grant['id']}", token, {"scope": merged})
        print(f"✓ Admin consent updated (scope='{merged}')")
        return

    # No existing grant - create one.
    body = {
        "clientId": login_sp_id,
        "consentType": "AllPrincipals",
        "resourceId": graph_sp_id,
        "scope": target_scope,
    }
    print("→ POST /oauth2PermissionGrants (grant admin consent tenant-wide)...")
    graph.graph_post("oauth2PermissionGrants", token, body)
    print(f"✓ Admin consent granted for: {target_scope}")


def ensure_service_principal(graph, token: str, app_id: str) -> str:
    existing = graph.find_service_principal_by_app_id(app_id, token)
    if existing:
        print(f"• Service principal already exists (id={existing['id']})")
        return existing["id"]
    print("→ POST /servicePrincipals (create SP for the login app)...")
    resp = graph.graph_post("servicePrincipals", token, {"appId": app_id})
    sp_id = resp.get("id")
    print(f"✓ Created service principal (id={sp_id})")
    return sp_id


def _resolve_service_scope_id(graph, token: str, service_sp_object_id: str, scope_name: str) -> str | None:
    """Look up the GUID of a delegated scope published by another SP.

    Used to translate the service app's `obo_access` scope name to the
    GUID that goes into requiredResourceAccess.
    """
    sp = graph.graph_get(
        f"servicePrincipals/{service_sp_object_id}",
        token,
        params={"$select": "oauth2PermissionScopes"},
    )
    for s in sp.get("oauth2PermissionScopes") or []:
        if s.get("value") == scope_name:
            return s.get("id")
    return None


def _augment_rra_with_service_scope(
    graph,
    token: str,
    object_id: str,
    service_client_id: str,
    service_scope_id: str,
) -> None:
    """Add a requiredResourceAccess entry for the service app's obo_access scope."""
    current = graph.graph_get(
        f"applications/{object_id}",
        token,
        params={"$select": "requiredResourceAccess"},
    )
    existing_rra = list(current.get("requiredResourceAccess") or [])

    service_entry = next(
        (r for r in existing_rra if r.get("resourceAppId") == service_client_id),
        None,
    )
    existing_scope_ids = set()
    if service_entry:
        existing_scope_ids = {
            a.get("id") for a in (service_entry.get("resourceAccess") or []) if a.get("type") == "Scope"
        }
    if service_scope_id in existing_scope_ids:
        print("• Delegated permission on service app's obo_access already declared")
        return

    others = [r for r in existing_rra if r.get("resourceAppId") != service_client_id]
    merged_access = [{"id": sid, "type": "Scope"} for sid in sorted(existing_scope_ids | {service_scope_id})]
    merged = others + [{"resourceAppId": service_client_id, "resourceAccess": merged_access}]

    print("→ PATCH /applications (declare delegated permission on service app's scope)...")
    graph.graph_patch(
        f"applications/{object_id}",
        token,
        {"requiredResourceAccess": merged},
    )
    print("✓ Added delegated permission")


def main() -> None:
    load_dotenv(ENV_FILE)
    graph = _load_helper()

    must_env("ENTRA_TENANT_ID")  # sanity: setup/02 should have populated
    x5t_s256 = must_env("X5T_S256_THUMBPRINT")
    label = os.environ.get("ENTRA_LOGIN_APP_LABEL") or DEFAULT_LOGIN_APP_LABEL

    if not CERT_DER_PATH.exists():
        print(
            f"✗ {CERT_DER_PATH.name} not found. Run setup/01_build_certificate.py first.",
            file=sys.stderr,
        )
        sys.exit(1)
    cert_der = CERT_DER_PATH.read_bytes()

    print(f"Cert:       {CERT_DER_PATH.name} ({len(cert_der)} bytes)")
    print(f"x5t#S256:   {x5t_s256}")
    print(f"Label:      {label}")
    print()

    print("→ Acquiring Microsoft Graph access token...")
    token = graph.get_graph_token()
    print("✓ Graph token acquired")

    print()
    existing = graph.find_application_by_display_name(label, token)
    if existing:
        object_id = existing["id"]
        app_id = existing["appId"]
        print(f"• Login web app already exists (objectId={object_id}, appId={app_id})")
        existing_creds = existing.get("keyCredentials") or []
    else:
        created = create_web_app(graph, token, label)
        object_id = created["id"]
        app_id = created["appId"]
        existing_creds = created.get("keyCredentials") or []

    print()
    upsert_key_credential(graph, token, object_id, existing_creds, cert_der)

    print()
    print("→ Resolving Microsoft Graph delegated scope IDs...")
    graph_sp_id, scope_map = get_graph_scope_id_map(graph, token)
    missing = [s for s in DELEGATED_GRAPH_SCOPES if s not in scope_map]
    if missing:
        print(
            f"✗ Microsoft Graph SP does not publish these scopes: {missing}. That's unexpected.",
            file=sys.stderr,
        )
        sys.exit(1)
    scope_ids = [scope_map[s] for s in DELEGATED_GRAPH_SCOPES]

    print()
    current_app = graph.graph_get(
        f"applications/{object_id}",
        token,
        params={"$select": "requiredResourceAccess"},
    )
    upsert_required_resource_access(
        graph,
        token,
        object_id,
        current_app.get("requiredResourceAccess") or [],
        MICROSOFT_GRAPH_APP_ID,
        scope_ids,
    )

    print()
    login_sp_id = ensure_service_principal(graph, token, app_id)

    print()
    grant_admin_consent_delegated(graph, token, login_sp_id, graph_sp_id, DELEGATED_GRAPH_SCOPES)

    # Additionally: the login web app needs delegated permission to the
    # SERVICE app's obo_access scope. Entra OBO requires the subject
    # (user) token to be audienced at the API being exchanged for. That
    # means the browser 3LO flow must request api://<service-app>/obo_access,
    # which only works if this login app is authorized for that scope.
    service_client_id = os.environ.get("ENTRA_SERVICE_CLIENT_ID", "").strip()
    service_sp_object_id = os.environ.get("ENTRA_SERVICE_SP_OBJECT_ID", "").strip()
    scope_name = os.environ.get("ENTRA_DELEGATED_SCOPE_NAME") or DEFAULT_DELEGATED_SCOPE_NAME

    if service_client_id and service_sp_object_id:
        print()
        print(f"→ Adding delegated permission on the service app's {scope_name!r} scope...")
        obo_scope_id = _resolve_service_scope_id(graph, token, service_sp_object_id, scope_name)
        if obo_scope_id:
            _augment_rra_with_service_scope(graph, token, object_id, service_client_id, obo_scope_id)
            print()
            print(f"→ Admin-consenting {scope_name!r} on the service app tenant-wide...")
            grant_admin_consent_delegated(graph, token, login_sp_id, service_sp_object_id, [scope_name])
        else:
            print(
                f"⚠ Could not find scope {scope_name!r} on the service app's SP. "
                f"Re-run setup/02a first to expose it, then re-run this script.",
                file=sys.stderr,
            )
    else:
        print()
        print(
            "⚠ ENTRA_SERVICE_CLIENT_ID / ENTRA_SERVICE_SP_OBJECT_ID not in .env - "
            "skipping OBO scope grant. Run setup/02a first, then re-run this script "
            "for OBO to work end-to-end.",
            file=sys.stderr,
        )

    print()
    write_env_var("ENTRA_LOGIN_APP_OBJECT_ID", object_id)
    write_env_var("ENTRA_LOGIN_CLIENT_ID", app_id)

    print()
    print("=" * 70)
    print("  Summary - step 02b: Entra login web app (OBO 3LO)")
    print("=" * 70)
    print("  What was created (or reused):")
    print(f"    Label                     : {label}")
    print(f"    Application (client) ID   : {app_id}")
    print(f"    App object ID             : {object_id}")
    print(f"    Service Principal ID      : {login_sp_id}")
    print("    Sign-in audience          : AzureADMyOrg (single-tenant)")
    print("    Platform                  : Web")
    print(f"    Redirect URI              : {PLACEHOLDER_REDIRECT_URI}")
    print("                                ↳ setup/04b will replace this with")
    print("                                  AgentCore Identity's callback URL")
    print("    Client credential         : Same X.509 cert as the service app")
    print(f"                                (x5t#S256 = {x5t_s256})")
    print(f"    Delegated Graph perms     : {', '.join(DELEGATED_GRAPH_SCOPES)}")
    if service_client_id:
        print(f"    Delegated service perms   : {scope_name!r} on the service app")
        print("                                (aud=api://<service-clientId> user")
        print("                                 tokens needed for OBO)")
    print("    Admin consent             : granted (tenant-wide, for both)")
    print()
    print("  Where to inspect it in the Entra admin center:")
    print(f"    App registrations → '{label}' → Overview")
    print("      Direct URL:")
    print("        https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/")
    print(f"        ApplicationMenuBlade/~/Overview/appId/{app_id}")
    print("    Certificates & secrets → Certificates (should show the cert)")
    print("    API permissions → all 5 Graph scopes with green 'Granted' state")
    print("    Authentication → Web platform → Redirect URIs (placeholder for now)")
    print()
    print("  Written to .env:")
    print(f"    ENTRA_LOGIN_APP_OBJECT_ID={object_id}")
    print(f"    ENTRA_LOGIN_CLIENT_ID={app_id}")
    print()
    print("  Why this step matters:")
    print("    OBO needs a user access token as the RFC 7523 assertion.")
    print("    That token is minted by an authorization_code flow: user opens")
    print("    this app in their browser, signs in, Entra returns an auth code,")
    print("    AgentCore Identity exchanges the code using this app's client_id")
    print("    and PRIVATE_KEY_JWT (same cert, same KMS key).")
    print()
    print("  Next steps:")
    print("    python setup/03_create_provider_m2m.py")
    print("    python setup/04_create_provider_obo.py")
    print("    python setup/04b_create_provider_client.py")


if __name__ == "__main__":
    main()
