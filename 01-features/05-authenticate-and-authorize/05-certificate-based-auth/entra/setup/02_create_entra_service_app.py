"""
Create the Entra ID application registration that authenticates to
Microsoft Entra as a confidential client for M2M and OBO. Upload the
KMS-signed certificate from setup/01 to the app's keyCredentials.

What this script does:
  1. Reads SIGNING_KMS_KEY_ARN and X5T_S256_THUMBPRINT from .env
     (populated by setup/00 + setup/01).
  2. Acquires a Microsoft Graph access token via _entra_graph.get_graph_token
     (which prefers GRAPH_ACCESS_TOKEN, falls back to `az account
     get-access-token`).
  3. Discovers ENTRA_TENANT_ID from the current az session if it's not
     already set in .env - and writes it back.
  4. Finds an existing app registration with label
     ENTRA_SERVICE_APP_LABEL, or creates one via
     POST /v1.0/applications with signInAudience=AzureADMyOrg.
  5. Reads entra_cert.der (built by setup/01) and PATCHes the app's
     keyCredentials to include the AsymmetricX509Cert entry. Idempotent:
     matches on customKeyIdentifier = base64(SHA-256(cert-DER)).
  6. Ensures a service principal exists for this app (POST
     /v1.0/servicePrincipals with just {appId}). We need the SP object
     ID in setup/02a to grant admin consent for app roles.
  7. Writes ENTRA_TENANT_ID, ENTRA_SERVICE_APP_OBJECT_ID,
     ENTRA_SERVICE_CLIENT_ID, and ENTRA_SERVICE_SP_OBJECT_ID back to .env.

Idempotent: safe to re-run. If the app already exists with the correct
cert, the second run prints "already exists" and moves on.

Usage:
    python setup/02_create_entra_service_app.py
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
DEFAULT_SERVICE_APP_LABEL = "AgentCore Identity Private Key JWT Sample"


def _load_helper():
    """Load setup/_entra_graph.py without needing it to be an importable package."""
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
    """Build the Graph keyCredential object for the uploaded certificate.

    Microsoft Graph's keyCredential resource. Fields:
      - keyId              - Graph auto-assigns a GUID on write.
      - customKeyIdentifier - base64(SHA-256(cert-DER)). Used by Graph
                              internally + used here for idempotency.
      - type = AsymmetricX509Cert
      - usage = Verify
      - key = base64(cert-DER)
    """
    encoded = base64.b64encode(cert_der).decode("ascii")
    key_id = base64.b64encode(hashlib.sha256(cert_der).digest()).decode("ascii")
    return {
        "customKeyIdentifier": key_id,
        "type": "AsymmetricX509Cert",
        "usage": "Verify",
        "displayName": "AgentCore Identity PRIVATE_KEY_JWT (sample)",
        "key": encoded,
    }


def create_service_app(graph, token: str, label: str) -> dict:
    """POST /applications. Single-tenant, API-services shape."""
    body = {
        "displayName": label,
        "signInAudience": "AzureADMyOrg",
        # No default web/spa/publicClient redirect URIs - this is an
        # API-services app used by the client_credentials + jwt-bearer
        # flows. setup/02b creates the separate web app for 3LO.
    }
    print(f"→ POST /applications (create '{label}')...")
    resp = graph.graph_post("applications", token, body)
    print(f"✓ Created app registration (appId={resp.get('appId')})")
    return resp


def upsert_key_credential(graph, token: str, object_id: str, existing_creds: list, cert_der: bytes) -> None:
    """PATCH keyCredentials to include our cert, preserving other entries."""
    target = build_key_credential(cert_der)
    target_id = target["customKeyIdentifier"]

    already_present = any((c.get("customKeyIdentifier") == target_id) for c in existing_creds)
    if already_present:
        print("• Certificate already present in the app's keyCredentials")
        return

    # Preserve other entries but strip fields Graph doesn't accept on PATCH.
    sanitized = [{k: v for k, v in c.items() if v is not None and k != "keyId"} for c in existing_creds]
    updated = sanitized + [target]

    print("→ PATCH /applications (add certificate to keyCredentials)...")
    graph.graph_patch(f"applications/{object_id}", token, {"keyCredentials": updated})
    print("✓ Certificate added to keyCredentials")


def ensure_service_principal(graph, token: str, app_id: str) -> str:
    """Ensure /servicePrincipals contains an SP for this appId. Return SP id."""
    existing = graph.find_service_principal_by_app_id(app_id, token)
    if existing:
        print(f"• Service principal already exists (id={existing['id']})")
        return existing["id"]

    print("→ POST /servicePrincipals (create SP for the app)...")
    resp = graph.graph_post("servicePrincipals", token, {"appId": app_id})
    sp_id = resp.get("id")
    print(f"✓ Created service principal (id={sp_id})")
    return sp_id


def main() -> None:
    load_dotenv(ENV_FILE)
    graph = _load_helper()

    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    x5t_s256 = must_env("X5T_S256_THUMBPRINT")
    label = os.environ.get("ENTRA_SERVICE_APP_LABEL") or DEFAULT_SERVICE_APP_LABEL

    if not CERT_DER_PATH.exists():
        print(
            f"✗ {CERT_DER_PATH.name} not found. Run setup/01_build_certificate.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cert_der = CERT_DER_PATH.read_bytes()

    print(f"KMS key:   {key_arn}")
    print(f"Cert:      {CERT_DER_PATH.name} ({len(cert_der)} bytes)")
    print(f"x5t#S256:  {x5t_s256}")
    print(f"Label:     {label}")
    print()

    print("→ Acquiring Microsoft Graph access token...")
    token = graph.get_graph_token()
    print("✓ Graph token acquired")

    # Discover tenant ID from the az session if it isn't set already.
    tenant_id = os.environ.get("ENTRA_TENANT_ID", "").strip()
    if not tenant_id or tenant_id.startswith("00000000"):
        discovered = graph.get_tenant_id()
        if discovered:
            tenant_id = discovered
            print(f"• Discovered tenant from az session: {tenant_id}")
        else:
            print(
                "⚠ ENTRA_TENANT_ID is unset and az session couldn't provide one.\n"
                "  Set ENTRA_TENANT_ID in .env manually and re-run.",
                file=sys.stderr,
            )
            sys.exit(1)

    print()
    existing = graph.find_application_by_display_name(label, token)
    if existing:
        object_id = existing["id"]
        app_id = existing["appId"]
        print(f"• App registration already exists (objectId={object_id}, appId={app_id})")
        existing_creds = existing.get("keyCredentials") or []
    else:
        created = create_service_app(graph, token, label)
        object_id = created["id"]
        app_id = created["appId"]
        existing_creds = created.get("keyCredentials") or []

    print()
    upsert_key_credential(graph, token, object_id, existing_creds, cert_der)

    print()
    sp_object_id = ensure_service_principal(graph, token, app_id)

    print()
    write_env_var("ENTRA_TENANT_ID", tenant_id)
    write_env_var("ENTRA_SERVICE_APP_OBJECT_ID", object_id)
    write_env_var("ENTRA_SERVICE_CLIENT_ID", app_id)
    write_env_var("ENTRA_SERVICE_SP_OBJECT_ID", sp_object_id)

    print()
    print("=" * 70)
    print("  Summary - step 02: Entra service app registration")
    print("=" * 70)
    print("  What was created (or reused):")
    print(f"    Label                     : {label}")
    print(f"    Application (client) ID   : {app_id}")
    print(f"    App object ID             : {object_id}")
    print(f"    Service Principal ID      : {sp_object_id}")
    print("    Sign-in audience          : AzureADMyOrg (single-tenant)")
    print("    Client credential         : X.509 cert (private_key_jwt)")
    print(f"                                x5t#S256 = {x5t_s256}")
    print()
    print("  Where to inspect it in the Entra admin center:")
    print(f"    Microsoft Entra ID → App registrations → '{label}'")
    print("    Certificates & secrets → Certificates tab (should show the cert)")
    print("    Overview tab shows both Application ID and Object ID (they differ!)")
    print("    Direct URL:")
    print("      https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/")
    print(f"      ApplicationMenuBlade/~/Overview/appId/{app_id}")
    print()
    print("  Written to .env:")
    print(f"    ENTRA_TENANT_ID={tenant_id}")
    print(f"    ENTRA_SERVICE_APP_OBJECT_ID={object_id}")
    print(f"    ENTRA_SERVICE_CLIENT_ID={app_id}")
    print(f"    ENTRA_SERVICE_SP_OBJECT_ID={sp_object_id}")
    print()
    print("  Why this step matters:")
    print("    This is the confidential client that authenticates to Entra's")
    print("    /token endpoint on every M2M or OBO call. Its appId is the")
    print("    iss/sub of the KMS-signed JWT client assertion; the cert we")
    print("    just uploaded is what Entra uses to verify that assertion.")
    print()
    print("  Next step:")
    print("    python setup/02a_configure_entra_permissions.py")
    print("    (exposes an API URI, adds the 'm2m' app role, and grants")
    print("     admin consent so client_credentials can request .default)")


if __name__ == "__main__":
    main()
