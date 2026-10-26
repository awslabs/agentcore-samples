"""
Generate a click-by-click walkthrough for configuring Okta by hand,
so you don't have to hand over an SSWS admin token.

This is the alternative to running setup/01, 01a, and 01b via the API.
It fetches your KMS signing key's public half, derives the JWK you need
to paste into the Okta admin console, and prints every screen you'll
touch - with exact field values and JSON payloads.

What this script does NOT do:
  - Change anything in your Okta tenant (no API calls).
  - Create the KMS key (run setup/00 first).
  - Create the AgentCore providers (run setup/02, 03, and optionally 03b
    after Okta is configured).

Prerequisites:
  - setup/00_provision_signing_key.py has run - SIGNING_KMS_KEY_ARN is
    in .env.
  - AWS credentials with kms:GetPublicKey against that key.

Usage:
    python setup/print_manual_okta_instructions.py           # both flows
    python setup/print_manual_okta_instructions.py --m2m     # M2M only
    python setup/print_manual_okta_instructions.py --obo     # OBO only
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import boto3
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

DEFAULT_SERVICE_APP_LABEL = "AgentCore Identity Private Key JWT Sample"
DEFAULT_LOGIN_APP_LABEL = "AgentCore Identity Private Key JWT Login App"
DEFAULT_POLICY_NAME = "AgentCore Identity Private Key JWT Sample"
DEFAULT_AUTH_POLICY_NAME = "AgentCore Identity Private Key JWT Sample Auth"
DEFAULT_RULE_NAME = "pkjwt-sample-client-credentials"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. Run setup/00 first.", file=sys.stderr)
        sys.exit(1)
    return value


# ─────────────────────────── JWK derivation ─────────────────────────
# Duplicated (intentionally) from setup/01 so this helper stands alone
# and doesn't need to import numeric-prefixed setup modules.


def b64url_uint(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def kms_public_key_to_jwk(der_public_key: bytes) -> dict:
    pubkey = load_der_public_key(der_public_key)
    if not isinstance(pubkey, rsa.RSAPublicKey):
        raise TypeError("This sample only supports RSA keys (RS256).")
    numbers = pubkey.public_numbers()
    kid = hashlib.sha256(der_public_key).hexdigest()[:16]
    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": kid,
        "n": b64url_uint(numbers.n),
        "e": b64url_uint(numbers.e),
    }


def get_kms_public_key_der(kms, key_arn: str) -> bytes:
    resp = kms.get_public_key(KeyId=key_arn)
    if resp.get("KeyUsage") != "SIGN_VERIFY":
        raise RuntimeError(f"KMS key {key_arn} has KeyUsage={resp.get('KeyUsage')!r}, expected SIGN_VERIFY.")
    return resp["PublicKey"]


# ─────────────────────────── printers ───────────────────────────────


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


def print_intro(*, base_url: str, jwk: dict, kms_arn: str) -> None:
    _header("Manual Okta configuration walkthrough")
    print(
        "You are configuring your Okta tenant WITHOUT the setup scripts\n"
        "making Okta API calls on your behalf. Everything on the AgentCore\n"
        "side (KMS key, credential providers, workload identity) is still\n"
        "handled by setup/00, 02, 03, 03b.\n"
    )
    print("Environment:")
    print(f"  Okta admin console : {base_url}/admin")
    print(f"  KMS key            : {kms_arn}")
    print(f"  JWK kid            : {jwk['kid']}")
    print()
    print("Two Okta apps get created below. Both use PRIVATE_KEY_JWT")
    print("client authentication, and both register the same JWK (same")
    print("KMS key underneath). They differ in application type and grant")
    print("types.")


def print_jwk_block(jwk: dict) -> None:
    _header("Public key JWK to paste into Okta")
    print("Paste this exact JSON as the Public Key on each app under")
    print("General → Client Credentials → Public Keys → Add key → JSON.")
    print()
    print(json.dumps(jwk, indent=2))


def print_service_app_instructions(*, service_label: str) -> None:
    _header("Step A - create the SERVICE app (M2M + OBO caller)")

    _substep(1, "Applications → Applications → Create App Integration")
    print("  Sign-in method                   : OIDC - OpenID Connect")
    print("  Application type                 : API Services")
    print("  → Next")

    _substep(2, "General settings")
    print(f"  App integration name             : {service_label}")
    print("  Grant type (Client acting on behalf of itself):")
    print("    ☑ Client Credentials")
    print("    ☑ Token Exchange")
    print("  → Save")

    _substep(3, "General tab → Client Credentials → Client authentication")
    print("  Client authentication            : Public key / Private key")
    print("  Public Keys                      : Add key → paste the JWK")
    print("                                     printed above → Save.")
    print("  → Save")

    _substep(4, "Copy identifiers for .env")
    print("  On the General tab, copy:")
    print("    Client ID → OKTA_SERVICE_APP_CLIENT_ID  (and OKTA_SERVICE_APP_ID)")
    print("    (For DCR-created OIDC clients the client_id doubles as the appId.)")


def print_as_config_instructions(
    *,
    as_id: str,
    scope: str,
    policy_name: str,
    rule_name: str,
    base_url: str,
) -> None:
    _header(f"Step B - configure Custom Authorization Server '{as_id}'")

    _substep(1, "Security → API → Authorization Servers → " + as_id + " → Scopes tab")
    print("  Add Scope:")
    print(f"    Name                : {scope}")
    print("    Description         : Sample scope for AgentCore Identity PRIVATE_KEY_JWT test")
    print("    User consent        : Implicit")
    print("    Include in public metadata: ☑")
    print("    → Create")

    _substep(2, "→ Access Policies tab → Add New Access Policy")
    print(f"  Name        : {policy_name}")
    print("  Description : Auto-created by the PRIVATE_KEY_JWT sample. Grants the sample")
    print("                service app permission to use client_credentials +")
    print("                token_exchange for the sample scope.")
    print("  Assign to   : Add Client → search 'AgentCore Identity Private Key")
    print("                JWT Sample' → select your SERVICE app → Save.")

    _substep(3, "→ Access Policies → your new policy → Add Rule")
    print(f"  Rule Name                              : {rule_name}")
    print("  IF Grant type is                       : Client acting on behalf")
    print("                                             of itself (Client")
    print("                                             Credentials + Token")
    print("                                             Exchange)")
    print("                                           AND")
    print("                                           Client acting on behalf")
    print("                                             of a user (Authorization")
    print("                                             Code)")
    print("  AND User is                            : Any user assigned the app")
    print("  AND Scopes requested                   :")
    print("    Any scopes  ← pick this OR list them explicitly:")
    print(f"      {scope}, openid, profile, email, offline_access")
    print("  THEN Access token lifetime is          : 60 minutes (default)")
    print("  Refresh token lifetime                 : 90 days (default)")
    print("  → Create Rule")

    _substep(4, "Verify")
    print(f"  {base_url}/admin/oauth2/as/{as_id}/policies")
    print(f"  Policy '{policy_name}' should show your service app under Clients,")
    print(f"  and rule '{rule_name}' should list all three grant types.")


def print_web_app_instructions(
    *,
    login_label: str,
    auth_policy_name: str,
    base_url: str,
) -> None:
    _header("Step C - create the WEB app (browser 3LO for OBO)")
    print(
        "OBO needs a user access token as the subject_token. The web app is\n"
        "what your user signs into during 3LO. Same JWK, different grant types."
    )

    _substep(1, "Applications → Applications → Create App Integration")
    print("  Sign-in method                   : OIDC - OpenID Connect")
    print("  Application type                 : Web Application")
    print("  → Next")

    _substep(2, "General settings")
    print(f"  App integration name             : {login_label}")
    print("  Grant type:")
    print("    ☑ Authorization Code")
    print("    ☑ Refresh Token")
    print("  Sign-in redirect URIs            : https://placeholder.example.com/set-later")
    print("                                     (setup/03b will print the")
    print("                                      real AgentCore-managed URL")
    print("                                      to paste here.)")
    print("  Sign-out redirect URIs           : (leave blank)")
    print("  Assignments                      : Limit access to selected groups")
    print("                                     OR Skip group assignment for now")
    print("  → Save")

    _substep(3, "General tab → Client Credentials → Client authentication")
    print("  Client authentication            : Public key / Private key")
    print("  Public Keys                      : Add key → paste the SAME JWK")
    print("                                     as the service app (same kid,")
    print("                                     same n, same e) → Save.")

    _substep(4, "Assignments tab")
    print("  Assign → People → search your Okta login → Assign.")
    print("  (Explicit user assignment is more reliable than Federation Broker")
    print("   Mode. See README troubleshooting for the rationale.)")

    _substep(5, "Security → Authentication Policies → Add a policy")
    print(f"  Name         : {auth_policy_name}")
    print("  Description  : Permissive authentication policy for the AgentCore")
    print("                 Identity Private Key JWT sample login app.")
    print("  Rules        : Add Rule → default 'Catch-all Rule':")
    print("                   IF User's group membership is        : Any group")
    print("                   THEN Access is                       : Allowed")
    print("                   with authentication provider being   : Any")
    print("                   factor mode                          : Password /")
    print("                                                          any 1 factor")
    print("                   Reauthentication frequency           : 2 hours")

    _substep(6, "Applications → your web app → Sign On tab → Authentication policy")
    print(f"  Edit → pick '{auth_policy_name}' → Save.")

    _substep(7, "Copy identifiers for .env")
    print("  On the General tab, copy:")
    print("    Client ID → OKTA_LOGIN_APP_CLIENT_ID  (and OKTA_LOGIN_APP_ID)")


def print_env_summary(
    *,
    service_label: str,
    login_label: str,
    scope: str,
    include_service: bool,
    include_web: bool,
) -> None:
    _header(".env values to fill in after the console clicks")
    print("Add / update these keys manually:")
    print()
    if include_service:
        print("  OKTA_SERVICE_APP_ID=<client_id from step A.4>")
        print("  OKTA_SERVICE_APP_CLIENT_ID=<same value as above>")
    print("  SIGNING_KID=<kid from the JWK block above>")
    print(f"  M2M_SCOPE={scope}")
    if include_web:
        print("  OKTA_LOGIN_APP_ID=<client_id from step C.7>")
        print("  OKTA_LOGIN_APP_CLIENT_ID=<same value as above>")
    print()
    print("Leave OKTA_API_TOKEN blank in .env - the remaining scripts")
    print("(setup/02, 03, and optionally 03b) don't need it.")


def print_next_steps(*, include_web: bool) -> None:
    _header("Next commands")
    print("Continue setting up the AgentCore identity side:")
    print()
    print("  python setup/02_create_provider_m2m.py")
    print("  python setup/03_create_provider_obo.py")
    if include_web:
        print("  python setup/03b_create_provider_client.py")
        print()
        print("setup/03b will print AgentCore's managed callback URL. Copy that")
        print("URL back into the login web app's Sign-in redirect URIs")
        print("(Applications → your web app → General → LOGIN → Sign-in")
        print("redirect URIs → Edit).")
    print()
    print("Then run the demos:")
    print("  python outbound_private_key_jwt_m2m.py")
    if include_web:
        print("  python get_okta_user_jwt.py")
        print("  python outbound_private_key_jwt_obo.py")


# ─────────────────────────── main ───────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a manual Okta admin console walkthrough for the sample.")
    parser.add_argument(
        "--m2m",
        action="store_true",
        help="Only print steps needed for the M2M demo (skip web app + Auth Policy).",
    )
    parser.add_argument(
        "--obo",
        action="store_true",
        help="Only print steps needed for the OBO demo.",
    )
    args = parser.parse_args()

    # Neither flag → both. Both flags → both. One flag → just that.
    include_service = True  # both flows need the service app
    include_web = not args.m2m or args.obo

    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")
    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    domain = must_env("OKTA_DOMAIN")
    as_id = os.environ.get("OKTA_AUTH_SERVER_ID") or "default"
    scope = os.environ.get("M2M_SCOPE") or "api:access"
    service_label = os.environ.get("OKTA_SERVICE_APP_LABEL") or DEFAULT_SERVICE_APP_LABEL
    login_label = os.environ.get("OKTA_LOGIN_APP_LABEL") or DEFAULT_LOGIN_APP_LABEL
    policy_name = os.environ.get("OKTA_POLICY_NAME") or DEFAULT_POLICY_NAME
    auth_policy_name = os.environ.get("OKTA_AUTH_POLICY_NAME") or DEFAULT_AUTH_POLICY_NAME
    rule_name = DEFAULT_RULE_NAME
    base_url = f"https://{domain}"

    kms = boto3.client("kms", region_name=region)
    der = get_kms_public_key_der(kms, key_arn)
    jwk = kms_public_key_to_jwk(der)

    print_intro(base_url=base_url, jwk=jwk, kms_arn=key_arn)
    print_jwk_block(jwk)

    if include_service:
        print_service_app_instructions(service_label=service_label)
        print_as_config_instructions(
            as_id=as_id,
            scope=scope,
            policy_name=policy_name,
            rule_name=rule_name,
            base_url=base_url,
        )
    if include_web:
        print_web_app_instructions(
            login_label=login_label,
            auth_policy_name=auth_policy_name,
            base_url=base_url,
        )
    print_env_summary(
        service_label=service_label,
        login_label=login_label,
        scope=scope,
        include_service=include_service,
        include_web=include_web,
    )
    print_next_steps(include_web=include_web)


if __name__ == "__main__":
    main()
