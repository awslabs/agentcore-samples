"""
Demo: perform an on-behalf-of (OBO) token exchange with Okta via RFC 8693
token exchange, with AgentCore identity authenticating to Okta's token
endpoint using PRIVATE_KEY_JWT.

What this script does:
  1. Reads the OBO credential provider name, workload name, and the caller's
     user JWT (OKTA_USER_JWT) from .env.
  2. Calls GetWorkloadAccessTokenForJWT - embeds the user JWT as the
     subject of the workload access token.
  3. Calls GetResourceOauth2Token with oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE.
     AgentCore identity then:
       - Builds a JWT client assertion signed by KMS (as in the M2M script).
       - Posts to Okta's token endpoint with:
           grant_type=urn:ietf:params:oauth:grant-type:token-exchange
           subject_token=<the user JWT you provided>
           subject_token_type=urn:ietf:params:oauth:token-type:jwt
           client_assertion=<KMS-signed JWT>
           client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
       - No actor_token is sent (actorTokenContent=NONE on the provider),
         so Okta does not emit an RFC 8693 `act` claim on the exchanged
         token.
  4. Prints the exchanged token and its decoded claims.

Getting a user JWT for testing (before running this script):

  Option 1 - Okta CLI:
      okta login
      okta apps login <native-app-client-id>
      # Copy the id_token from the output into .env as OKTA_USER_JWT.

  Option 2 - Password grant (developer orgs only, needs a password-grant
  enabled Okta app):
      curl -X POST 'https://<domain>/oauth2/<auth-server-id>/v1/token' \\
           -H 'Content-Type: application/x-www-form-urlencoded' \\
           -d 'grant_type=password' \\
           -d 'username=<user>' -d 'password=<pass>' \\
           -d 'client_id=<native-client-id>' \\
           -d 'scope=openid profile'

  Option 3 - Your existing frontend. Sign in as a normal user and copy the
  access_token or id_token from the browser's network tab.

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

    Okta will reject an expired subject_token with a clear message, but
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
            f"⚠ OKTA_USER_JWT looks expired (exp was {expired_for}s ago).\n"
            f"  Fetch a fresh token - see the module docstring for options.",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Perform an OBO token exchange with Okta via PRIVATE_KEY_JWT.")
    parser.add_argument(
        "--scope",
        default=None,
        help="OAuth scope to request. Defaults to OBO_SCOPE env var, or 'openid'.",
    )
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("OBO_PROVIDER_NAME")
    user_jwt = must_env("OKTA_USER_JWT")
    # Okta's Custom AS requires a custom scope for OBO too - 'openid' is
    # rejected on token-exchange same as on client_credentials.
    scope = args.scope or os.environ.get("OBO_SCOPE") or os.environ.get("M2M_SCOPE", "api:access")
    # For Okta OBO, the audience must match the Custom AS audience.
    audience = os.environ.get("OBO_AUDIENCE", "api://default")

    check_user_jwt_not_expired(user_jwt)

    data_client = boto3.client("bedrock-agentcore", region_name=region)

    print(f"Region:   {region}")
    print(f"Workload: {workload_name}")
    print(f"Provider: {provider_name}")
    print(f"Scope:    {scope}")

    user_claims = decode_jwt_claims(user_jwt)
    if user_claims:
        print(f"User:     sub={user_claims.get('sub')!r} cid={user_claims.get('cid')!r}")
    print()

    print("→ GetWorkloadAccessTokenForJWT (embedding user JWT as subject)...")
    workload_token = data_client.get_workload_access_token_for_jwt(
        workloadName=workload_name,
        userToken=user_jwt,
    )["workloadAccessToken"]
    print("✓ Received workload access token (carries the user JWT as subject)")

    print()
    print(f"→ GetResourceOauth2Token (oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE, provider={provider_name})...")
    # Two Okta-specific parameters that are load-bearing for OBO:
    #   audiences         → Okta requires 'api://default' for the Custom AS.
    #   subject_token_type→ Okta rejects the default 'jwt' type; needs
    #                       urn:...:token-type:access_token for standard
    #                       OBO with an access-token subject.
    resp = data_client.get_resource_oauth2_token(
        workloadIdentityToken=workload_token,
        resourceCredentialProviderName=provider_name,
        scopes=[scope],
        oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        audiences=[audience],
        customParameters={
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        },
    )
    access_token = resp.get("accessToken")
    if not access_token:
        print("✗ No accessToken in response:", file=sys.stderr)
        print(json.dumps(resp, indent=2, default=str), file=sys.stderr)
        sys.exit(1)

    print("✓ Received exchanged token from Okta")
    print()

    print("=" * 70)
    print("  On-behalf-of token retrieved via PRIVATE_KEY_JWT client assertion")
    print("=" * 70)
    print(f"  Preview: {access_token[:40]}...{access_token[-10:]}")
    print()

    claims = decode_jwt_claims(access_token)
    claim_meaning = {
        "iss": "Okta issuer - the Custom AS URL",
        "aud": "Audience - API this token is intended for (api://default for Custom AS)",
        "cid": "Client ID - the SERVICE app (proves the caller is your agent)",
        "sub": "Subject - the END USER (proves 'on behalf of')",
        "uid": "Okta user ID - internal identifier for the end user",
        "scp": "Scopes granted",
        "iat": "Issued at (unix time)",
        "exp": "Expires at (unix time)",
    }
    if claims:
        print("  Decoded claims:")
        for key, meaning in claim_meaning.items():
            if key in claims:
                print(f"    {key:<6}: {claims[key]}")
                print(f"    {'':<6}  ↳ {meaning}")
        # 'act' claim is only emitted when the provider is configured with
        # actorTokenContent != NONE, and it identifies the acting agent.
        # Setup/03 leaves it disabled - call that out explicitly.
        if "act" not in claims:
            print("    act   : (not emitted - provider actorTokenContent=NONE)")
            print("            ↳ To emit an RFC 8693 'act' claim identifying the")
            print("              agent, set actorTokenContent to M2M or")
            print("              AWS_IAM_ID_TOKEN_JWT on the OBO provider.")
    print()
    print("  What just happened:")
    print("    1. GetWorkloadAccessTokenForJWT embedded your user JWT as the")
    print("       subject of a workload access token.")
    print("    2. AgentCore identity built a JWT client assertion (KMS-signed)")
    print("       against the SERVICE app's client_id.")
    print("    3. POSTed to Okta's /token with:")
    print("         grant_type         = urn:ietf:params:oauth:grant-type:token-exchange")
    print("         subject_token      = <your user JWT>")
    print("         subject_token_type = urn:ietf:params:oauth:token-type:access_token")
    print(f"         audience           = {audience}")
    print("         client_assertion   = <signed JWT>")
    print(f"         scope              = {scope}")
    print("    4. Okta verified the assertion + subject_token, matched an AS")
    print("       Access Policy rule, and returned the token above.")
    print()
    print("  Why the claim shape matters:")
    print("    sub = the user  → downstream API can enforce user permissions")
    print("    cid = service app → downstream API can log which agent called it")
    print("    This is the OBO invariant: user identity carried forward through")
    print("    a chain of service calls without ever handing the user's raw")
    print("    credential to a downstream service.")


if __name__ == "__main__":
    main()
