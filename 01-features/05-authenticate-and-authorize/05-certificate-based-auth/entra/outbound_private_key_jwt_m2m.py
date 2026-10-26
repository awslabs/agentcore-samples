"""
Demo: obtain a downstream access token from Entra ID using the
client_credentials (M2M) grant, with AgentCore Identity signing the JWT
client assertion via PRIVATE_KEY_JWT (KMS-backed X.509 cert).

What this script does:
  1. Reads the M2M credential provider name and workload name from .env.
  2. Calls GetWorkloadAccessToken to get a short-lived workload identity
     token (identifies this agent, not any end user).
  3. Calls GetResourceOauth2Token with oauth2Flow=M2M and the M2M
     provider name. Under the hood AgentCore Identity:
       - Builds a JWT client assertion with iss=sub=clientId,
         aud=<Entra tenant's /token endpoint>, jti, iat, exp.
       - Adds "x5t#S256": X5T_S256_THUMBPRINT to the JWT header
         (from additionalHeaderClaims on the provider).
       - Calls kms:Sign against SIGNING_KMS_KEY_ARN to sign it (RS256).
       - Posts to Entra's /token with:
           grant_type=client_credentials
           client_assertion=<signed JWT>
           client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
           scope=api://<ENTRA_SERVICE_CLIENT_ID>/.default
  4. Prints the resulting access token and its decoded claims.

Prerequisites:
  - You have completed setup/00 through setup/03.
  - The Entra service app has the certificate uploaded (setup/02) and
    the 'm2m' app role granted admin consent (setup/02a).

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
    parser = argparse.ArgumentParser(description="Fetch an M2M access token from Entra via AgentCore PRIVATE_KEY_JWT.")
    parser.add_argument(
        "--scope",
        default=None,
        help=("OAuth scope to request. Defaults to api://<ENTRA_SERVICE_CLIENT_ID>/.default"),
    )
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("M2M_PROVIDER_NAME")
    client_id = must_env("ENTRA_SERVICE_CLIENT_ID")
    # Entra's client_credentials grant only accepts .default scopes.
    # Default to the service app's own .default so the resulting token
    # carries roles=['m2m'] (from setup/02a).
    scope = args.scope or os.environ.get("M2M_SCOPE") or f"api://{client_id}/.default"

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

    print("✓ Received access token from Entra")
    print()

    print("=" * 70)
    print("  M2M access token retrieved via PRIVATE_KEY_JWT client assertion")
    print("=" * 70)
    print(f"  Preview: {access_token[:40]}...{access_token[-10:]}")
    print()

    claims = decode_jwt_claims(access_token)
    claim_meaning = {
        "iss": "Entra issuer - sts.windows.net/<tenant>/",
        "aud": "Audience - the API this token is for (api://<clientId>)",
        "appid": "AppId - your service app (also the caller identity)",
        "appidacr": "App auth method - '2' means certificate (PRIVATE_KEY_JWT)",
        "roles": "App roles granted (from setup/02a's m2m role)",
        "oid": "Object ID of the calling principal (the service principal)",
        "tid": "Tenant ID",
        "iat": "Issued at (unix time)",
        "exp": "Expires at (unix time)",
    }
    if claims:
        print("  Decoded claims:")
        for key, meaning in claim_meaning.items():
            if key in claims:
                print(f"    {key:<9}: {claims[key]}")
                print(f"    {'':<9}  ↳ {meaning}")
    print()
    print("  What just happened:")
    print("    1. AgentCore Identity built a JWT client assertion:")
    print(f"         iss = sub = {claims.get('appid') or '<service app appId>'}")
    print(f"         aud = https://login.microsoftonline.com/{claims.get('tid') or '<tenant>'}/oauth2/v2.0/token")
    print("         jti, iat, exp populated")
    print("         header includes  alg = RS256  and  x5t#S256 = <thumbprint>")
    print("    2. Called kms:Sign against SIGNING_KMS_KEY_ARN. The private key")
    print("       never left KMS.")
    print("    3. POSTed to Entra's /token with:")
    print("         grant_type            = client_credentials")
    print("         client_assertion      = <signed JWT>")
    print("         client_assertion_type = urn:ietf:params:oauth:")
    print("                                 client-assertion-type:jwt-bearer")
    print(f"         scope                 = {scope}")
    print("    4. Entra matched the x5t#S256 header to the uploaded")
    print("       certificate, verified the assertion, and returned this")
    print("       app-only token.")
    print()
    print("  What this proves:")
    print("    appidacr = '2' confirms the client authenticated with a")
    print("    certificate (not a client secret). No secret was read,")
    print("    transmitted, or stored anywhere. Rotating credentials is")
    print("    now a KMS key rotation, not a shared-secret handoff.")


if __name__ == "__main__":
    main()
