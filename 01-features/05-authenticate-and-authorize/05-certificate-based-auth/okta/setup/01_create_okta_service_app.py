"""
Create an Okta OIDC service application configured for PRIVATE_KEY_JWT
client authentication, with the KMS-hosted public key registered in one
atomic call.

What this script does:
  1. Reads SIGNING_KMS_KEY_ARN from .env.
  2. Calls kms:GetPublicKey and converts the DER-encoded RSA public key
     into a JSON Web Key (JWK).
  3. Calls Okta's Dynamic Client Registration endpoint
     (POST /oauth2/v1/clients per RFC 7591) with:
       - client_name = the app label
       - grant_types = [client_credentials]
       - token_endpoint_auth_method = private_key_jwt
       - application_type = service
       - jwks.keys = [ <the JWK from step 2> ]
  4. Optionally grants Okta admin scopes to the new service app so it
     can obtain tokens for those scopes (see OKTA_SCOPES in .env).
  5. Writes OKTA_SERVICE_APP_ID, OKTA_SERVICE_APP_CLIENT_ID, and
     SIGNING_KID back into .env.

Why the dedicated DCR endpoint instead of /api/v1/apps:
  For OIDC apps that use private_key_jwt client authentication, Okta
  requires the JWKS to be present at app creation time. The Dynamic
  Client Registration endpoint accepts JWKS inline in the create
  request; the general /api/v1/apps endpoint rejects it. See
  https://developer.okta.com/blog/2021/11/15/private-key-jwt-python.

Idempotent: if an app with OKTA_SERVICE_APP_LABEL already exists in the
tenant, the script reuses it instead of creating a duplicate.

Usage:
    python setup/01_create_okta_service_app.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import boto3
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_LABEL = "AgentCore Identity Private Key JWT Sample"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def okta_headers(api_token: str) -> dict:
    return {
        "Authorization": f"SSWS {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def b64url_uint(value: int) -> str:
    """Encode an unsigned integer as base64url without padding, per JWK spec."""
    length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def kms_public_key_to_jwk(der_public_key: bytes) -> dict:
    """Convert a DER-encoded RSA SubjectPublicKeyInfo into a JWK dict.

    The kid is derived deterministically from SHA-256 of the DER bytes so
    the JWK is stable across script runs against the same KMS key.
    """
    pubkey = load_der_public_key(der_public_key)
    if not isinstance(pubkey, rsa.RSAPublicKey):
        raise TypeError("This sample only supports RSA keys (RS256). The KMS key returned a non-RSA public key.")
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


def find_app_by_label(base_url: str, api_token: str, label: str) -> dict | None:
    """Return the app JSON if an app with this label exists, else None."""
    resp = requests.get(
        f"{base_url}/api/v1/apps",
        headers=okta_headers(api_token),
        params={"q": label, "limit": 50},
        timeout=15,
    )
    if resp.status_code == 401:
        print(
            "✗ Okta returned 401. Check OKTA_API_TOKEN - an SSWS admin token "
            "with app-management permission is required.",
            file=sys.stderr,
        )
        sys.exit(1)
    resp.raise_for_status()
    for app in resp.json():
        if app.get("label") == label:
            return app
    return None


def create_service_app_via_dcr(base_url: str, api_token: str, label: str, jwk: dict) -> dict:
    """Create an OIDC service app via RFC 7591 Dynamic Client Registration.

    Okta hosts DCR at /oauth2/v1/clients and accepts SSWS admin tokens for
    authentication. The response is a standard DCR JSON body - client_id
    at the top level.
    """
    # grant_types includes both client_credentials (M2M) and the RFC 8693
    # token-exchange grant so the same app serves both flavors of demo.
    # Add token-exchange even when only M2M is used - it's a no-op if
    # unused, and having both up front avoids a second PUT to add it later.
    payload = {
        "client_name": label,
        "response_types": ["token"],
        "grant_types": [
            "client_credentials",
            "urn:ietf:params:oauth:grant-type:token-exchange",
        ],
        "token_endpoint_auth_method": "private_key_jwt",
        "application_type": "service",
        "jwks": {"keys": [jwk]},
    }
    resp = requests.post(
        f"{base_url}/oauth2/v1/clients",
        headers=okta_headers(api_token),
        data=json.dumps(payload),
        timeout=30,
    )
    if resp.status_code >= 400:
        print(
            f"✗ Okta POST /oauth2/v1/clients returned HTTP {resp.status_code}:\n  {resp.text[:600]}",
            file=sys.stderr,
        )
        resp.raise_for_status()
    return resp.json()


def grant_scope(base_url: str, api_token: str, client_id: str, scope: str) -> None:
    """Grant a single Okta admin scope to the service app.

    Uses POST /api/v1/apps/{id}/grants. Idempotent: 409 conflicts are
    treated as success.
    """
    payload = {
        "issuer": base_url,
        "scopeId": scope,
    }
    resp = requests.post(
        f"{base_url}/api/v1/apps/{client_id}/grants",
        headers=okta_headers(api_token),
        data=json.dumps(payload),
        timeout=15,
    )
    if resp.status_code in (200, 201):
        print(f"✓ Granted scope: {scope}")
    elif resp.status_code == 409:
        print(f"• Scope already granted: {scope}")
    else:
        print(
            f"⚠ Grant scope {scope} returned HTTP {resp.status_code}: {resp.text[:300]}",
            file=sys.stderr,
        )


def write_env_var(name: str, value: str) -> None:
    """Persist a KEY=VALUE line into .env. Create .env from example if needed."""
    if not ENV_FILE.exists():
        example = ENV_FILE.with_name("config.example.env")
        if not example.exists():
            print(
                f"ERROR: neither {ENV_FILE} nor {example} exists.",
                file=sys.stderr,
            )
            sys.exit(1)
        ENV_FILE.write_text(example.read_text())
        print(f"• Created {ENV_FILE.name} from config.example.env")

    text = ENV_FILE.read_text()
    pattern = rf"^{re.escape(name)}=.*$"
    replacement = f"{name}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        new_text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(new_text)
    print(f"✓ Wrote {name} to .env")


def main() -> None:
    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    domain = must_env("OKTA_DOMAIN")
    api_token = must_env("OKTA_API_TOKEN")
    label = os.environ.get("OKTA_SERVICE_APP_LABEL") or DEFAULT_LABEL

    if "-admin." in domain:
        print(
            f"✗ OKTA_DOMAIN={domain!r} looks like the Okta admin host.\n  Use the app-facing host (drop '-admin').",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = f"https://{domain}"

    # OKTA_SCOPES is a space-separated list of Okta admin scopes to grant.
    scopes = (os.environ.get("OKTA_SCOPES") or "").split()

    kms = boto3.client("kms", region_name=region)

    print(f"KMS key: {key_arn}")
    print(f"Okta:    {base_url}")
    print(f"Label:   {label}")
    if scopes:
        print(f"Scopes:  {', '.join(scopes)}")
    print()

    # Build the JWK from the KMS public key.
    der = get_kms_public_key_der(kms, key_arn)
    jwk = kms_public_key_to_jwk(der)
    print(f"✓ Built JWK (kid={jwk['kid']}, alg={jwk['alg']})")

    # Check idempotency: if an app with this label already exists, reuse it.
    existing = find_app_by_label(base_url, api_token, label)
    if existing:
        client_id = existing["credentials"]["oauthClient"]["client_id"]
        app_id = existing["id"]
        print(f"• Okta service app already exists: {app_id} (label={label!r})")
        print("  Skipping creation. If you need to rotate the JWK on this")
        print("  app, run cleanup.py first, then re-run this script.")
    else:
        print()
        print("→ Creating Okta OIDC service app via Dynamic Client Registration...")
        created = create_service_app_via_dcr(base_url, api_token, label, jwk)
        client_id = created.get("client_id")
        if not client_id:
            raise RuntimeError(f"DCR response missing client_id:\n{json.dumps(created, indent=2)}")
        # For DCR-created clients, the OAuth client_id also serves as the
        # /api/v1/apps/{id} identifier.
        app_id = client_id
        print(f"✓ Created Okta service app: {app_id}")

    print(f"  Client ID: {client_id}")

    # Grant requested scopes (best-effort).
    if scopes:
        print()
        for scope in scopes:
            grant_scope(base_url, api_token, client_id, scope)

    print()
    write_env_var("OKTA_SERVICE_APP_ID", app_id)
    write_env_var("OKTA_SERVICE_APP_CLIENT_ID", client_id)
    write_env_var("SIGNING_KID", jwk["kid"])

    print()
    print("=" * 70)
    print("  Summary - step 01: Okta OIDC service app")
    print("=" * 70)
    print("  What was created (or reused if label already existed):")
    print(f"    Label                     : {label}")
    print(f"    App / client ID           : {client_id}")
    print("    Application type          : service")
    print("    Grant types               : client_credentials,")
    print("                                urn:ietf:params:oauth:grant-type:token-exchange")
    print("    Client auth method        : private_key_jwt")
    print(f"    JWK kid                   : {jwk['kid']}  (RS256)")
    print()
    print("  Where to inspect it in the Okta admin console:")
    print(f"    Applications → Applications → search {label!r}")
    print("    General tab → Client Credentials should show:")
    print("      Client authentication : Public key / Private key")
    print(f"      Public Keys           : one JWK, kid = {jwk['kid']}")
    print("    Direct URL:")
    print(f"      {base_url}/admin/app/oidc_client/instance/{app_id}")
    print()
    print("  Written to .env:")
    print(f"    OKTA_SERVICE_APP_ID={app_id}")
    print(f"    OKTA_SERVICE_APP_CLIENT_ID={client_id}")
    print(f"    SIGNING_KID={jwk['kid']}")
    print()
    print("  Why this step matters:")
    print("    This is the confidential client that authenticates to Okta's")
    print("    /token endpoint on every M2M or OBO call. Its client_id is the")
    print("    iss/sub of the KMS-signed JWT client assertion, and the JWK we")
    print("    just published is what Okta uses to verify that assertion.")
    print()
    print("  Next step:")
    print("    python setup/01a_configure_okta_auth_server.py")
    print("    (registers the M2M scope and creates the AS Access Policy)")


if __name__ == "__main__":
    main()
