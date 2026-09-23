"""
Create an AgentCore Identity credential provider for the OBO (RFC 7523
JWT bearer + on_behalf_of) grant flow, authenticated to Entra ID with
PRIVATE_KEY_JWT.

Entra's OBO flow is RFC 7523 jwt-bearer, distinct from Okta's RFC 8693
token-exchange:

  Entra:  grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
          assertion=<user JWT>
          requested_token_use=on_behalf_of
          client_assertion=<KMS-signed JWT>
          client_assertion_type=...:jwt-bearer

  Okta:   grant_type=urn:ietf:params:oauth:grant-type:token-exchange
          subject_token=<user JWT>
          subject_token_type=urn:ietf:params:oauth:token-type:access_token
          client_assertion=<KMS-signed JWT>

AgentCore Identity models these as different grantType enums on the
onBehalfOfTokenExchangeConfig:

  Entra:  JWT_AUTHORIZATION_GRANT   (this script)
  Okta:   TOKEN_EXCHANGE            (see the Okta sample's setup/03)

Under JWT_AUTHORIZATION_GRANT, ACPS does NOT send an actor_token, so
there is no actorTokenContent to configure. The runtime demo passes
requested_token_use=on_behalf_of via customParameters - see
outbound_private_key_jwt_obo.py.

What this script does:
  1. Ensures the workload identity exists.
  2. Creates a CustomOauth2 credential provider OBO_PROVIDER_NAME with:
       - Same private-key-JWT + KMS-key + x5t#S256 header setup as the
         M2M provider (setup/03).
       - clientId points at the SAME service app (Entra OBO does not
         use a distinct client for the caller - the app that does
         client_credentials also does OBO).
       - onBehalfOfTokenExchangeConfig.grantType = JWT_AUTHORIZATION_GRANT.

Idempotent: if the provider already exists, updates its configuration.

Usage:
    python setup/04_create_provider_obo.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def discovery_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"


def build_provider_config(
    *,
    tenant_id: str,
    client_id: str,
    kms_key_arn: str,
    x5t_s256: str,
) -> dict:
    """Build the oauth2ProviderConfigInput dict for an OBO provider on Entra.

    Broken out from main() so it can be unit-tested without needing AWS
    credentials or a real Entra tenant.

    Note the deliberate absence of tokenExchangeGrantTypeConfig - that
    sub-block is only meaningful for TOKEN_EXCHANGE. Under
    JWT_AUTHORIZATION_GRANT, ACPS never sends an actor_token, so there
    is no actorTokenContent to set.
    """
    return {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": discovery_url(tenant_id)},
            "clientId": client_id,
            "clientAuthenticationMethod": "PRIVATE_KEY_JWT",
            "privateKeyJwtConfig": {
                "privateKeySource": {"kmsKeySource": {"kmsKeyArn": kms_key_arn}},
                "signingAlgorithm": "RS256",
                "additionalHeaderClaims": {"x5t#S256": x5t_s256},
            },
            "onBehalfOfTokenExchangeConfig": {
                "grantType": "JWT_AUTHORIZATION_GRANT",
            },
        }
    }


def ensure_workload_identity(client, name: str) -> None:
    try:
        client.create_workload_identity(name=name)
        print(f"✓ Created workload identity: {name}")
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        msg = e.response["Error"].get("Message", "")
        already_exists = code in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        } or ("already exists" in msg.lower())
        if already_exists:
            print(f"• Workload identity already exists: {name}")
        else:
            raise


def upsert_provider(client, *, name: str, config: dict) -> str:
    try:
        resp = client.create_oauth2_credential_provider(
            name=name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=config,
        )
        print(f"✓ Created credential provider: {name}")
        return resp["credentialProviderArn"]
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        msg = e.response["Error"].get("Message", "")
        already_exists = code in {
            "ConflictException",
            "ResourceAlreadyExistsException",
        } or ("already exists" in msg.lower())
        if not already_exists:
            raise

    resp = client.update_oauth2_credential_provider(
        name=name,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput=config,
    )
    print(f"✓ Updated credential provider: {name}")
    return resp["credentialProviderArn"]


def main() -> None:
    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    tenant_id = must_env("ENTRA_TENANT_ID")
    client_id = must_env("ENTRA_SERVICE_CLIENT_ID")
    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    x5t_s256 = must_env("X5T_S256_THUMBPRINT")
    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("OBO_PROVIDER_NAME")

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region:   {region}")
    print(f"Tenant:   {tenant_id}")
    print(f"Client:   {client_id}")
    print(f"Workload: {workload_name}")
    print(f"Provider: {provider_name}")
    print()

    ensure_workload_identity(ac_control, workload_name)

    config = build_provider_config(
        tenant_id=tenant_id,
        client_id=client_id,
        kms_key_arn=key_arn,
        x5t_s256=x5t_s256,
    )
    arn = upsert_provider(ac_control, name=provider_name, config=config)

    print()
    print("=" * 70)
    print("  Summary - step 04: OBO credential provider (Entra)")
    print("=" * 70)
    print("  What was created / updated on AgentCore Identity:")
    print(f"    Provider name     : {provider_name}")
    print(f"    Provider ARN      : {arn}")
    print("    Vendor            : CustomOauth2")
    print("    Client auth       : PRIVATE_KEY_JWT (same KMS key as M2M)")
    print(f"    Client ID         : {client_id}  (same service app as M2M)")
    print(f"    KMS key           : {key_arn}")
    print(f"    Assertion header  : x5t#S256 = {x5t_s256}")
    print(f"    Discovery URL     : {discovery_url(tenant_id)}")
    print("    Grant type used   : urn:ietf:params:oauth:grant-type:jwt-bearer")
    print("    Provider config   : JWT_AUTHORIZATION_GRANT (no actor_token)")
    print()
    print("  Where to inspect it:")
    print("    aws bedrock-agentcore-control get-oauth2-credential-provider \\")
    print(f"      --name {provider_name} --region {region}")
    print()
    print("  Written to .env:")
    print("    (no new keys - provider name is user-supplied)")
    print()
    print("  Why this step matters:")
    print("    outbound_private_key_jwt_obo.py uses this provider to perform")
    print("    the RFC 7523 jwt-bearer exchange. The user token is passed as")
    print("    the `assertion` parameter (not `subject_token` like Okta), and")
    print("    `requested_token_use=on_behalf_of` is passed via customParameters.")
    print("    Entra returns a new access token whose `sub` is the user and")
    print("    whose `appid` is your service app - the caller-on-behalf-of-user")
    print("    identity shape that makes chained service calls work cleanly.")
    print()
    print("  Next steps:")
    print("    M2M-only path: run the M2M demo now.")
    print("      python outbound_private_key_jwt_m2m.py")
    print()
    print("    OBO path: continue setup, then run the OBO demo.")
    print("      python setup/04b_create_provider_client.py")
    print("      python get_entra_user_jwt.py")
    print("      python outbound_private_key_jwt_obo.py")


if __name__ == "__main__":
    main()
