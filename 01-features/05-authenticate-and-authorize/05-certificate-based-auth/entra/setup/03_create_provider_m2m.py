"""
Create an AgentCore Identity credential provider for the M2M
(client_credentials) grant flow, authenticated to Entra ID with
PRIVATE_KEY_JWT.

What this script does:
  1. Ensures a workload identity exists (WORKLOAD_NAME).
  2. Creates a CustomOauth2 credential provider named M2M_PROVIDER_NAME
     with:
       - clientAuthenticationMethod = PRIVATE_KEY_JWT
       - clientId                   = service app's Application (client) ID
       - signingAlgorithm           = RS256
       - additionalHeaderClaims.x5t#S256 = the cert thumbprint from setup/01
       - discovery URL              = tenant-specific v2 endpoint
       - No onBehalfOfTokenExchangeConfig - this provider is
         client_credentials only.
  3. No additionalPayloadClaims - Entra defaults the assertion `aud` to
     the discovered token endpoint, which is already the correct value
     for v2.

Idempotent: if the provider already exists, updates its configuration.

Usage:
    python setup/03_create_provider_m2m.py
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
    """Build the oauth2ProviderConfigInput dict for an M2M PRIVATE_KEY_JWT provider.

    Broken out from main() so it can be unit-tested without needing AWS
    credentials or a real Entra tenant.
    """
    return {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": discovery_url(tenant_id)},
            "clientId": client_id,
            "clientAuthenticationMethod": "PRIVATE_KEY_JWT",
            "privateKeyJwtConfig": {
                "privateKeySource": {"kmsKeySource": {"kmsKeyArn": kms_key_arn}},
                "signingAlgorithm": "RS256",
                # x5t#S256 is Entra's preferred cert-identifier header. The
                # SHA-1 sibling "x5t" also works; we use the modern form.
                "additionalHeaderClaims": {"x5t#S256": x5t_s256},
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

    tenant_id = must_env("ENTRA_TENANT_ID")
    client_id = must_env("ENTRA_SERVICE_CLIENT_ID")
    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    x5t_s256 = must_env("X5T_S256_THUMBPRINT")
    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("M2M_PROVIDER_NAME")

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
    print("  Summary - step 03: M2M credential provider (Entra)")
    print("=" * 70)
    print("  What was created / updated on AgentCore Identity:")
    print(f"    Workload identity : {workload_name}")
    print(f"    Provider name     : {provider_name}")
    print(f"    Provider ARN      : {arn}")
    print("    Vendor            : CustomOauth2")
    print("    Client auth       : PRIVATE_KEY_JWT (KMS-signed)")
    print(f"    Client ID         : {client_id}")
    print(f"    KMS key           : {key_arn}")
    print(f"    Assertion header  : x5t#S256 = {x5t_s256}")
    print(f"    Discovery URL     : {discovery_url(tenant_id)}")
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
    print("    On every GetResourceOauth2Token call, AgentCore Identity builds")
    print("    a JWT client assertion (iss=sub=clientId, aud=Entra's tenant")
    print("    /token endpoint), signs it with kms:Sign, and posts it to Entra")
    print("    with grant_type=client_credentials + scope=api://<clientId>/.default.")
    print("    Entra verifies the assertion against the certificate uploaded")
    print("    in setup/02 (matched by x5t#S256).")
    print()
    print("  Next step:")
    print("    python setup/04_create_provider_obo.py")


if __name__ == "__main__":
    main()
