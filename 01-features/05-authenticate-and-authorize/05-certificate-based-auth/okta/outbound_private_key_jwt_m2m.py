"""
Demo: obtain a downstream access token from Okta using the client_credentials
(M2M) grant, with AgentCore identity signing the JWT client assertion via
PRIVATE_KEY_JWT.

What this script does:
  1. Reads the M2M credential provider name and workload name from .env.
  2. Calls GetWorkloadAccessToken to get a short-lived workload identity
     token (identifies this agent, not any end user).
  3. Calls GetResourceOauth2Token with oauth2Flow=M2M and the M2M provider
     name. Under the hood AgentCore identity:
       - Builds a JWT client assertion with iss=sub=clientId, aud=token
         endpoint, jti, iat, exp.
       - Adds "kid": SIGNING_KID to the JWT header (from additionalHeaderClaims).
       - Calls kms:Sign against SIGNING_KMS_KEY_ARN to sign it (RS256).
       - Posts to Okta's token endpoint with grant_type=client_credentials,
         client_assertion, and client_assertion_type=urn:ietf:params:oauth:
         client-assertion-type:jwt-bearer.
  4. Prints the resulting access token and its decoded claims.

Prerequisites:
  - You have completed setup/00 through setup/02.
  - The Okta service app is configured with the JWK from setup/01 and its
    Client Authentication is set to "Public key / Private key".
  - The Okta service app is authorized for the M2M scope you request below
    (Applications → your app → Okta API Scopes / Grants).

Usage:
    python outbound_private_key_jwt_m2m.py [--scope <scope>]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def decode_jwt_claims(token: str) -> dict:
    """Decode a JWT payload without verifying the signature (display only)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, IndexError, TypeError):
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch an M2M access token from Okta via AgentCore PRIVATE_KEY_JWT.")
    parser.add_argument(
        "--scope",
        default=None,
        help="OAuth scope to request. Defaults to M2M_SCOPE env var, or 'api:access'.",
    )
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("M2M_PROVIDER_NAME")
    # Okta's Custom AS rejects the OIDC 'openid' scope for client_credentials
    # grants - the scope must be one you register on the AS (or one of
    # the built-in okta.* admin scopes if you target the Org AS). We use
    # a custom scope named api:access to mirror the Okta PRIVATE_KEY_JWT test guide.
    # Create it under Security → API → Authorization Servers → default →
    # Scopes tab, and grant it via an Access Policy rule that allows
    # client_credentials for your service app.
    scope = args.scope or os.environ.get("M2M_SCOPE", "api:access")

    data_client = boto3.client("bedrock-agentcore", region_name=region)

    print(f"Region:   {region}")
    print(f"Workload: {workload_name}")
    print(f"Provider: {provider_name}")
    print(f"Scope:    {scope}")
    print()

    print("→ GetWorkloadAccessToken...")
    workload_token = data_client.get_workload_access_token(
        workloadName=workload_name,
    )["workloadAccessToken"]
    print("✓ Received workload access token")

    print()
    print(f"→ GetResourceOauth2Token (oauth2Flow=M2M, provider={provider_name})...")
    resp = data_client.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=provider_name,
        scopes=[scope],
        oauth2Flow="M2M",
    )
    access_token = resp.get("accessToken")
    if not access_token:
        print("✗ No accessToken in response:", file=sys.stderr)
        print(json.dumps(resp, indent=2, default=str), file=sys.stderr)
        sys.exit(1)

    print("✓ Received access token from Okta")
    print()

    print("=" * 70)
    print("  M2M access token retrieved via PRIVATE_KEY_JWT client assertion")
    print("=" * 70)
    print(f"  Preview: {access_token[:40]}...{access_token[-10:]}")
    print()

    claims = decode_jwt_claims(access_token)
    claim_meaning = {
        "iss": "Okta issuer - the Custom AS URL",
        "aud": "Audience - API this token is intended for",
        "cid": "Client ID - the service app (also the caller identity)",
        "scp": "Scopes granted",
        "sub": "Subject - for client_credentials, equals cid",
        "iat": "Issued at (unix time)",
        "exp": "Expires at (unix time)",
    }
    if claims:
        print("  Decoded claims:")
        for key, meaning in claim_meaning.items():
            if key in claims:
                print(f"    {key:<6}: {claims[key]}")
                print(f"    {'':<6}  ↳ {meaning}")
    print()
    print("  What just happened:")
    print("    1. AgentCore identity built a JWT client assertion:")
    print(f"         iss = sub = {claims.get('cid') or '<service app client_id>'}")
    print("         aud = Okta's /token endpoint (from OIDC discovery)")
    print("         jti, iat, exp populated")
    print("         header includes  alg = RS256  and  kid = SIGNING_KID")
    print("    2. Called kms:Sign against SIGNING_KMS_KEY_ARN. The private key")
    print("       never left KMS.")
    print("    3. POSTed to Okta's /token with:")
    print("         grant_type            = client_credentials")
    print("         client_assertion      = <signed JWT>")
    print("         client_assertion_type = urn:ietf:params:oauth:")
    print("                                 client-assertion-type:jwt-bearer")
    print("         scope                 = " + scope)
    print("    4. Okta verified the assertion using the JWK we registered in")
    print("       setup/01, matched an AS Access Policy rule, and returned the")
    print("       access token above.")
    print()
    print("  What this proves:")
    print("    No client secret was read, transmitted, or stored anywhere on")
    print("    the AgentCore side. Rotating credentials is now a KMS key")
    print("    rotation, not a shared-secret handoff.")


if __name__ == "__main__":
    main()
