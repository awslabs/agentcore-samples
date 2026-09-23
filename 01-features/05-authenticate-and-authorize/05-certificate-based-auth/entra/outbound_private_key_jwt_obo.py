"""
Demo: perform an on-behalf-of (OBO) token exchange with Entra ID via
RFC 7523 JWT bearer grant + on_behalf_of, with AgentCore Identity
authenticating to Entra's token endpoint using PRIVATE_KEY_JWT.

Entra's OBO is RFC 7523 jwt-bearer (NOT RFC 8693 token-exchange like
Okta):

    grant_type          = urn:ietf:params:oauth:grant-type:jwt-bearer
    assertion           = <the user's access token>
    requested_token_use = on_behalf_of
    client_assertion    = <KMS-signed JWT proving the caller identity>
    client_assertion_type = urn:ietf:params:oauth:client-assertion-type:jwt-bearer
    scope               = <downstream resource>/.default

The provider on the AgentCore side (setup/04) is configured with
onBehalfOfTokenExchangeConfig.grantType = JWT_AUTHORIZATION_GRANT. This
script passes `requested_token_use=on_behalf_of` via customParameters
because that parameter is Entra-specific - the AgentCore Identity API
doesn't have a dedicated field for it.

What this script does:
  1. Reads the OBO credential provider name, workload name, and the
     caller's user JWT (ENTRA_USER_JWT) from .env.
  2. Calls GetWorkloadAccessTokenForJWT - embeds the user JWT as the
     subject of the workload access token.
  3. Calls GetResourceOauth2Token with:
       - oauth2Flow = ON_BEHALF_OF_TOKEN_EXCHANGE
       - scopes = downstream resource .default (Microsoft Graph by default)
       - customParameters = {"requested_token_use": "on_behalf_of"}
     AgentCore Identity then:
       - Builds a JWT client assertion (KMS-signed via x5t#S256 header)
         against the service app's client_id.
       - Posts to Entra's /token with the user JWT in `assertion`
         (not `subject_token`).
       - No actor_token is sent - Entra doesn't use one.
  4. Prints the exchanged token and its decoded claims.

Getting a user JWT for testing (before running this script):

  Run: python get_entra_user_jwt.py

  That helper drives the AgentCore Identity USER_FEDERATION flow -
  opens your browser, you sign in, and the token is written to
  ENTRA_USER_JWT in .env.

Usage:
    python outbound_private_key_jwt_obo.py [--scope <scope>]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
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
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, IndexError, TypeError):
        return {}


def check_user_jwt_not_expired(user_jwt: str) -> None:
    """Warn (don't fail) if the user JWT looks expired.

    Entra will reject an expired assertion with a clear message, but
    surfacing it here saves a round trip.
    """
    claims = decode_jwt_claims(user_jwt)
    exp = claims.get("exp")
    if not exp:
        return
    now = int(time.time())
    if exp <= now:
        expired_for = now - exp
        print(
            f"⚠ ENTRA_USER_JWT looks expired (exp was {expired_for}s ago).\n"
            f"  Re-run get_entra_user_jwt.py to mint a fresh token.",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Perform an OBO token exchange with Entra via PRIVATE_KEY_JWT.")
    parser.add_argument(
        "--scope",
        default=None,
        help=("OAuth scope for the downstream resource. Defaults to https://graph.microsoft.com/.default"),
    )
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("OBO_PROVIDER_NAME")
    user_jwt = must_env("ENTRA_USER_JWT")
    # Entra OBO requires a downstream .default scope. Default to Graph
    # so the sample can demonstrate OBO end-to-end without needing a
    # second custom API. Override with --scope for other resources.
    scope = args.scope or os.environ.get("OBO_SCOPE") or "https://graph.microsoft.com/.default"

    check_user_jwt_not_expired(user_jwt)

    data_client = boto3.client("bedrock-agentcore", region_name=region)

    print(f"Region:   {region}")
    print(f"Workload: {workload_name}")
    print(f"Provider: {provider_name}")
    print(f"Scope:    {scope}")

    user_claims = decode_jwt_claims(user_jwt)
    if user_claims:
        print(
            f"User:     sub={user_claims.get('sub')!r} "
            f"upn={user_claims.get('upn')!r} "
            f"appid={user_claims.get('appid')!r}"
        )
    print()

    print("→ GetWorkloadAccessTokenForJWT (embedding user JWT as subject)...")
    workload_token = data_client.get_workload_access_token_for_jwt(
        workloadName=workload_name,
        userToken=user_jwt,
    )["workloadAccessToken"]
    print("✓ Received workload access token (carries the user JWT as subject)")

    print()
    print(f"→ GetResourceOauth2Token (oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE, provider={provider_name})...")
    # requested_token_use=on_behalf_of is Entra's flag that this is an
    # OBO call rather than a plain jwt-bearer grant. AgentCore Identity
    # forwards customParameters to the token endpoint as extra form data.
    resp = data_client.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=provider_name,
        scopes=[scope],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        customParameters={"requested_token_use": "on_behalf_of"},
    )
    access_token = resp.get("accessToken")
    if not access_token:
        print("✗ No accessToken in response:", file=sys.stderr)
        print(json.dumps(resp, indent=2, default=str), file=sys.stderr)
        sys.exit(1)

    print("✓ Received exchanged token from Entra")
    print()

    print("=" * 70)
    print("  On-behalf-of token retrieved via PRIVATE_KEY_JWT client assertion")
    print("=" * 70)
    print(f"  Preview: {access_token[:40]}...{access_token[-10:]}")
    print()

    claims = decode_jwt_claims(access_token)
    claim_meaning = {
        "iss": "Entra issuer - sts.windows.net/<tenant>/",
        "aud": "Audience - the downstream resource (e.g. https://graph.microsoft.com)",
        "appid": "AppId - your service app (proves the caller is your agent)",
        "app_displayname": "Display name of the calling app",
        "appidacr": "App auth method - '2' means certificate (PRIVATE_KEY_JWT)",
        "idtyp": "'user' - this is a delegated token, not app-only",
        "name": "End user's display name",
        "upn": "End user's UPN (User Principal Name)",
        "sub": "Subject - hash-based ID for the user in this app's context",
        "oid": "Object ID of the end user (stable across apps in the tenant)",
        "scp": "Delegated scopes granted",
        "iat": "Issued at (unix time)",
        "exp": "Expires at (unix time)",
    }
    if claims:
        print("  Decoded claims:")
        for key, meaning in claim_meaning.items():
            if key in claims:
                print(f"    {key:<15}: {claims[key]}")
                print(f"    {'':<15}  ↳ {meaning}")
    print()
    print("  What just happened:")
    print("    1. GetWorkloadAccessTokenForJWT embedded your user JWT as the")
    print("       subject of a workload access token.")
    print("    2. AgentCore Identity built a JWT client assertion (KMS-signed)")
    print("       against the SERVICE app's client_id.")
    print("    3. POSTed to Entra's /token with:")
    print("         grant_type          = urn:ietf:params:oauth:grant-type:jwt-bearer")
    print("         assertion           = <your user JWT>")
    print("         requested_token_use = on_behalf_of")
    print("         client_assertion    = <signed JWT>")
    print(f"         scope               = {scope}")
    print("    4. Entra verified the client assertion (via the uploaded cert),")
    print("       verified the user assertion, matched the requested scope's")
    print("       API permissions, and returned this delegated user token.")
    print()
    print("  Why the claim shape matters:")
    print("    idtyp = 'user' + upn/name = the user's identity   →  'on behalf of'")
    print("    appid = your service app                          →  the caller")
    print("    aud   = downstream resource (e.g. Graph)          →  the target API")
    print("    This is the OBO invariant: user identity carried forward through")
    print("    a chain of service calls without ever handing the user's raw")
    print("    credential to a downstream service.")


if __name__ == "__main__":
    main()
