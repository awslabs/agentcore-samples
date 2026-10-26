"""
Create an AgentCore identity credential provider for the M2M (client
credentials) grant flow, authenticated to Okta with PRIVATE_KEY_JWT.

What this script does:
  1. Ensures a workload identity exists (WORKLOAD_NAME).
  2. Creates a CustomOauth2 credential provider named M2M_PROVIDER_NAME
     with clientAuthenticationMethod = PRIVATE_KEY_JWT, pointing at the
     KMS key from SIGNING_KMS_KEY_ARN and passing kid = SIGNING_KID in
     additionalHeaderClaims.
  3. No onBehalfOfTokenExchangeConfig is set - this provider is for the
     client_credentials grant only.

Idempotent: if the provider already exists, updates its configuration.

Usage:
    python setup/02_create_provider_m2m.py
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


def build_provider_config(
    *,
    discovery_url: str,
    client_id: str,
    kms_key_arn: str,
    kid: str,
) -> dict:
    """Build the oauth2ProviderConfigInput dict for an M2M PRIVATE_KEY_JWT provider.

    Broken out from main() so it can be unit-tested without needing AWS
    credentials or a real Okta tenant.
    """
    return {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": discovery_url},
            "clientId": client_id,
            "clientAuthenticationMethod": "PRIVATE_KEY_JWT",
            "privateKeyJwtConfig": {
                "privateKeySource": {
                    "kmsKeySource": {"kmsKeyArn": kms_key_arn},
                },
                "signingAlgorithm": "RS256",
                "additionalHeaderClaims": {"kid": kid},
            },
        }
    }


def discovery_url(domain: str, auth_server_id: str) -> str:
    return f"https://{domain}/oauth2/{auth_server_id}/.well-known/openid-configuration"


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


def upsert_provider(
    client,
    *,
    name: str,
    config: dict,
) -> str:
    """Create-or-update a CustomOauth2 provider. Returns its ARN."""
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

    domain = must_env("OKTA_DOMAIN")
    auth_server_id = must_env("OKTA_AUTH_SERVER_ID")
    client_id = must_env("OKTA_SERVICE_APP_CLIENT_ID")
    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    kid = must_env("SIGNING_KID")
    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("M2M_PROVIDER_NAME")

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Region:   {region}")
    print(f"Okta:     {domain} / auth server {auth_server_id!r}")
    print(f"Workload: {workload_name}")
    print(f"Provider: {provider_name}")
    print()

    ensure_workload_identity(ac_control, workload_name)

    config = build_provider_config(
        discovery_url=discovery_url(domain, auth_server_id),
        client_id=client_id,
        kms_key_arn=key_arn,
        kid=kid,
    )
    arn = upsert_provider(ac_control, name=provider_name, config=config)

    print()
    print("=" * 70)
    print("  Summary - step 02: M2M credential provider")
    print("=" * 70)
    print("  What was created / updated on AgentCore identity:")
    print(f"    Workload identity : {workload_name}")
    print(f"    Provider name     : {provider_name}")
    print(f"    Provider ARN      : {arn}")
    print("    Vendor            : CustomOauth2")
    print("    Client auth       : PRIVATE_KEY_JWT (KMS-signed)")
    print(f"    Client ID         : {client_id}")
    print(f"    KMS key           : {key_arn}")
    print(f"    JWK kid header    : {kid}")
    print(f"    Discovery URL     : {discovery_url(domain, auth_server_id)}")
    print("    Grant type used   : client_credentials (M2M)")
    print()
    print("  Where to inspect it:")
    print("    No console UI in this preview. Verify with:")
    print("      aws bedrock-agentcore-control get-oauth2-credential-provider \\")
    print(f"        --name {provider_name} --region {region}")
    print()
    print("  Written to .env:")
    print("    (no new keys - provider name is user-supplied)")
    print()
    print("  Why this step matters:")
    print("    outbound_private_key_jwt_m2m.py points at this provider by name.")
    print("    On every GetResourceOauth2Token call, AgentCore identity builds")
    print("    the JWT client assertion, calls kms:Sign on the ARN above, and")
    print("    posts to Okta's /token endpoint using this configuration.")
    print()
    print("  Next step:")
    print("    python setup/03_create_provider_obo.py")


if __name__ == "__main__":
    main()
