"""
Generate a click-by-click walkthrough for configuring Entra ID by hand,
so you don't have to hand over Graph API permissions or run `az login`.

This is the alternative to running setup/02, 02a, 02b, and setup/04b's
redirectUri wiring. Everything on the AgentCore side (KMS key, cert
build, credential providers, workload identity) is still handled by
setup/00, 01, 03, 04, 04b.

What this script does NOT do:
  - Change anything in your Entra tenant (no Graph API calls).
  - Create the KMS key (run setup/00 first).
  - Create the AgentCore providers (run setup/03, 04, 04b after Entra
    is configured).

Prerequisites:
  - setup/00_provision_signing_key.py has run - SIGNING_KMS_KEY_ARN in .env.
  - setup/01_build_certificate.py has run - entra_cert.pem is on disk.
    (This script does NOT rebuild the cert; run setup/01 first.)

Usage:
    python setup/print_manual_entra_instructions.py           # both flows
    python setup/print_manual_entra_instructions.py --m2m     # M2M only
    python setup/print_manual_entra_instructions.py --obo     # OBO only
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
CERT_PEM_PATH = ROOT / "entra_cert.pem"
CERT_DER_PATH = ROOT / "entra_cert.der"

DEFAULT_SERVICE_APP_LABEL = "AgentCore Identity Private Key JWT Sample"
DEFAULT_LOGIN_APP_LABEL = "AgentCore Identity Private Key JWT Login App"
DEFAULT_APP_ROLE_NAME = "m2m"
DEFAULT_DELEGATED_SCOPE_NAME = "obo_access"
PLACEHOLDER_REDIRECT_URI = "https://placeholder.example.com/set-by-setup-04b"


def _hr(char: str = "─", width: int = 72) -> str:
    return char * width


def _header(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _substep(n: int, title: str) -> None:
    print()
    print(f"[{n}] {title}")
    print(_hr("─", 72))


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. Run setup/00 + 01 first.", file=sys.stderr)
        sys.exit(1)
    return value


def load_cert() -> tuple[str, bytes]:
    """Return (PEM text, DER bytes) of the KMS-signed cert."""
    if not CERT_PEM_PATH.exists():
        print(
            f"✗ {CERT_PEM_PATH.name} not found. Run setup/01_build_certificate.py first.",
            file=sys.stderr,
        )
        sys.exit(1)
    pem_text = CERT_PEM_PATH.read_text()
    if CERT_DER_PATH.exists():
        der = CERT_DER_PATH.read_bytes()
    else:
        # Extract DER from the PEM.
        from asn1crypto import pem as _pem

        _, _, der = _pem.unarmor(pem_text.encode("ascii"))
    return pem_text, der


def print_intro(*, tenant_id: str, kms_arn: str, x5t_s256: str) -> None:
    _header("Manual Entra ID configuration walkthrough")
    print(
        "You are configuring your Entra tenant WITHOUT the setup scripts\n"
        "making Microsoft Graph API calls on your behalf. Everything on\n"
        "the AgentCore side (KMS key, cert, credential providers, workload\n"
        "identity) is still handled by setup/00, 01, 03, 04, 04b.\n"
    )
    print("Environment:")
    print("  Entra admin center : https://entra.microsoft.com")
    print(f"  Tenant ID          : {tenant_id or '(unset - fill in .env before running providers)'}")
    print(f"  KMS key            : {kms_arn}")
    print(f"  x5t#S256 thumbprint: {x5t_s256}")
    print()
    print("  If you want the SHA-1 fingerprint that the Entra admin portal")
    print("  displays (Certificates & Secrets tab), run:")
    print(f"    openssl x509 -in {CERT_PEM_PATH.name} -noout -fingerprint -sha1")
    print()
    print("Two Entra apps get created below. Both use PRIVATE_KEY_JWT")
    print("client authentication, and both upload the same certificate.")
    print("They differ in application platform and grant flows.")


def print_cert_block(pem_text: str) -> None:
    _header("Certificate to upload to Entra")
    print(f"Both apps below take the same cert. It lives at {CERT_PEM_PATH.name}")
    print("in this folder. When Entra asks you to upload a certificate,")
    print("choose that file. For reference, its contents are:")
    print()
    for line in pem_text.rstrip().splitlines():
        print(f"    {line}")


def print_service_app_instructions(*, service_label: str, app_role: str, obo_scope: str) -> None:
    _header("Step A - create the SERVICE app (M2M + OBO caller)")

    _substep(1, "Entra admin center → App registrations → New registration")
    print(f"  Name                        : {service_label}")
    print("  Supported account types     : Accounts in this organizational")
    print("                                directory only (Single tenant)")
    print("  Redirect URI                : (leave blank)")
    print("  → Register")
    print()
    print("  On the Overview blade, copy:")
    print("    Application (client) ID → ENTRA_SERVICE_CLIENT_ID in .env")
    print("    Directory (tenant) ID   → ENTRA_TENANT_ID in .env")
    print("    Object ID               → ENTRA_SERVICE_APP_OBJECT_ID in .env")

    _substep(2, "Certificates & secrets → Certificates tab → Upload certificate")
    print(f"  Upload the {CERT_PEM_PATH.name} file from this folder → Add.")
    print("  The portal displays a SHA-1 fingerprint after upload. To verify")
    print("  it matches your local cert, run the openssl command shown above.")

    _substep(3, "Expose an API → Add an Application ID URI")
    print("  Application ID URI          : accept the default")
    print("                                api://<Application (client) ID>")
    print("  → Save")

    _substep(4, "App roles → Create app role  (for M2M)")
    print(f"  Display name                : {app_role}")
    print("  Allowed member types        : Applications")
    print(f"  Value                       : {app_role}")
    print("  Do you want to enable       : ☑ (Yes)")
    print("  → Apply")

    _substep(5, f"Expose an API → Add a scope  ({obo_scope!r} - for OBO)")
    print("  This delegated scope is what browser 3LO requests as")
    print(f"  api://<clientId>/{obo_scope}. The resulting user token has")
    print("  aud=api://<clientId>, which is what Entra OBO requires as")
    print("  its subject token audience.")
    print()
    print(f"  Scope name                  : {obo_scope}")
    print("  Who can consent             : Admins and users")
    print("  Admin consent display name  : Access sample API as user")
    print("  Admin consent description   : Allows the app to access this API")
    print("                                on behalf of the signed-in user.")
    print("  User consent display name   : Access sample API as you")
    print("  State                       : Enabled")
    print("  → Add scope")

    _substep(6, "API permissions → Add a permission → APIs my organization uses")
    print(f"  Select {service_label}")
    print("  → Application permissions")
    print(f"  → check {app_role} → Add permissions")

    _substep(7, "API permissions → Add a permission → Microsoft Graph")
    print("  → Delegated permissions")
    print("  Check these five scopes:")
    print("    - openid")
    print("    - profile")
    print("    - email")
    print("    - offline_access")
    print("    - User.Read")
    print("  → Add permissions")
    print()
    print("  These delegated permissions on the SERVICE app are what Entra")
    print("  checks during the OBO exchange to decide whether the middle-")
    print("  tier can hand out downstream tokens. Without them, OBO returns")
    print("  HTTP 400 even though the user token itself is valid.")

    _substep(8, "API permissions → Grant admin consent for <tenant name>")
    print("  Click the 'Grant admin consent for <tenant>' button at the")
    print("  top of the permissions list → confirm.")
    print(f"  Both the {app_role} app role and all five Graph delegated")
    print("  permissions should show 'Granted for <tenant>' in green.")


def print_login_app_instructions(*, login_label: str, service_label: str, obo_scope: str) -> None:
    _header("Step B - create the WEB app (browser 3LO for OBO)")
    print(
        "OBO needs a user access token as the RFC 7523 assertion. The web app\n"
        "is what your user signs into during 3LO. Same cert, different grant\n"
        "types and permissions."
    )

    _substep(1, "Entra admin center → App registrations → New registration")
    print(f"  Name                        : {login_label}")
    print("  Supported account types     : Accounts in this organizational")
    print("                                directory only (Single tenant)")
    print("  Platform                    : Web")
    print(f"  Redirect URI                : {PLACEHOLDER_REDIRECT_URI}")
    print("                                (setup/04b will replace this later)")
    print("  → Register")
    print()
    print("  On the Overview blade, copy:")
    print("    Application (client) ID → ENTRA_LOGIN_CLIENT_ID in .env")
    print("    Object ID               → ENTRA_LOGIN_APP_OBJECT_ID in .env")

    _substep(2, "Certificates & secrets → Certificates → Upload certificate")
    print(f"  Upload the SAME {CERT_PEM_PATH.name} file → Add.")

    _substep(3, "API permissions → Add a permission → Microsoft Graph")
    print("  → Delegated permissions")
    print("  Check these five scopes:")
    print("    - openid")
    print("    - profile")
    print("    - email")
    print("    - offline_access")
    print("    - User.Read")
    print("  → Add permissions")

    _substep(4, "API permissions → Add a permission → APIs my organization uses")
    print(f"  Select {service_label!r} (the service app from Step A)")
    print("  → Delegated permissions")
    print(f"  → check {obo_scope} → Add permissions")
    print()
    print("  This is the delegated permission the browser 3LO flow requests")
    print(f"  (api://<serviceClientId>/{obo_scope}). Without it, the resulting")
    print("  user token has aud=graph, which Entra rejects as an OBO subject.")

    _substep(5, "API permissions → Grant admin consent for <tenant name>")
    print("  Click the 'Grant admin consent for <tenant>' button.")
    print(f"  All five Graph scopes AND the {obo_scope} permission should")
    print("  show 'Granted for <tenant>' in green.")

    _substep(6, "Authentication → Web platform → Redirect URIs")
    print("  The placeholder is fine for now.")
    print("  After you run `python setup/04b_create_provider_client.py`, that")
    print("  script will print the real AgentCore-managed callback URL -")
    print("  come back here, click 'Add URI', paste that URL, and Save.")


def print_env_summary(*, include_service: bool, include_web: bool) -> None:
    _header(".env values to fill in after the console clicks")
    print("Add / update these keys manually:")
    print()
    if include_service:
        print("  ENTRA_TENANT_ID=<Directory (tenant) ID from Step A.1>")
        print("  ENTRA_SERVICE_APP_OBJECT_ID=<Object ID from Step A.1>")
        print("  ENTRA_SERVICE_CLIENT_ID=<Application (client) ID from Step A.1>")
        print("  ENTRA_SERVICE_SP_OBJECT_ID=<see below>")
        print()
        print("  How to find the service principal Object ID:")
        print("    Entra admin center → Enterprise applications → search")
        print("      the service app label → Overview → 'Object ID'.")
        print("    (Enterprise applications shows Service Principals; App")
        print("     registrations shows Applications. They're different Object")
        print("     IDs for the same underlying identity.)")
    if include_web:
        print()
        print("  ENTRA_LOGIN_APP_OBJECT_ID=<Object ID from Step B.1>")
        print("  ENTRA_LOGIN_CLIENT_ID=<Application (client) ID from Step B.1>")


def print_next_steps(*, include_web: bool) -> None:
    _header("Next commands")
    print("Continue setting up the AgentCore side:")
    print()
    print("  python setup/03_create_provider_m2m.py")
    print("  python setup/04_create_provider_obo.py")
    if include_web:
        print("  python setup/04b_create_provider_client.py")
        print()
        print("setup/04b prints AgentCore's managed callback URL. Return to")
        print("your login web app's Authentication blade and add that URL as a")
        print("Redirect URI (replacing the placeholder).")
    print()
    print("Then run the demos:")
    print("  python outbound_private_key_jwt_m2m.py")
    if include_web:
        print("  python get_entra_user_jwt.py")
        print("  python outbound_private_key_jwt_obo.py")


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a manual Entra admin center walkthrough for the sample.")
    parser.add_argument(
        "--m2m",
        action="store_true",
        help="Only print steps needed for the M2M demo (skip web app).",
    )
    parser.add_argument(
        "--obo",
        action="store_true",
        help="Only print steps needed for the OBO demo (includes both apps).",
    )
    args = parser.parse_args()

    include_service = True
    include_web = not args.m2m or args.obo

    load_dotenv(ENV_FILE)
    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    tenant_id = os.environ.get("ENTRA_TENANT_ID", "").strip()
    if tenant_id.startswith("00000000"):
        tenant_id = ""  # placeholder from config.example.env

    service_label = os.environ.get("ENTRA_SERVICE_APP_LABEL") or DEFAULT_SERVICE_APP_LABEL
    login_label = os.environ.get("ENTRA_LOGIN_APP_LABEL") or DEFAULT_LOGIN_APP_LABEL
    app_role = os.environ.get("ENTRA_APP_ROLE_NAME") or DEFAULT_APP_ROLE_NAME
    obo_scope = os.environ.get("ENTRA_DELEGATED_SCOPE_NAME") or DEFAULT_DELEGATED_SCOPE_NAME

    pem_text, der = load_cert()
    x5t_s256 = base64.urlsafe_b64encode(hashlib.sha256(der).digest()).rstrip(b"=").decode("ascii")

    print_intro(tenant_id=tenant_id, kms_arn=key_arn, x5t_s256=x5t_s256)
    print_cert_block(pem_text)

    if include_service:
        print_service_app_instructions(
            service_label=service_label,
            app_role=app_role,
            obo_scope=obo_scope,
        )
    if include_web:
        print_login_app_instructions(
            login_label=login_label,
            service_label=service_label,
            obo_scope=obo_scope,
        )

    print_env_summary(include_service=include_service, include_web=include_web)
    print_next_steps(include_web=include_web)


if __name__ == "__main__":
    main()
