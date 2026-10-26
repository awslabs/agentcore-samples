"""
Create an AgentCore identity credential provider for the OBO (RFC 8693 token
exchange) grant flow, authenticated to Okta with PRIVATE_KEY_JWT.

What this script does:
  1. Ensures the workload identity exists.
  2. Creates a CustomOauth2 credential provider named OBO_PROVIDER_NAME
     with clientAuthenticationMethod = PRIVATE_KEY_JWT and
     onBehalfOfTokenExchangeConfig set to grantType = TOKEN_EXCHANGE.
  3. actorTokenContent is set to NONE - no actor_token is sent in the
     exchange, so Okta does not emit an RFC 8693 `act` claim on the
     exchanged token. See ../README.md for a discussion of the other
     actorTokenContent modes (M2M, AWS_IAM_ID_TOKEN_JWT).

Idempotent: if the provider already exists, updates its configuration.

Usage:
    python setup/03_create_provider_obo.py
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
    """Build the oauth2ProviderConfigInput dict for an OBO PRIVATE_KEY_JWT provider.

    The Okta OBO shape uses RFC 8693 token exchange (grantType =
    TOKEN_EXCHANGE). actorTokenContent = NONE means AgentCore identity
    does not add an actor_token parameter - sufficient for the sample.
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
            "onBehalfOfTokenExchangeConfig": {
                "grantType": "TOKEN_EXCHANGE",
                "tokenExchangeGrantTypeConfig": {
                    "actorTokenContent": "NONE",
                },
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

    domain = must_env("OKTA_DOMAIN")
    auth_server_id = must_env("OKTA_AUTH_SERVER_ID")
    client_id = must_env("OKTA_SERVICE_APP_CLIENT_ID")
    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    kid = must_env("SIGNING_KID")
    workload_name = must_env("WORKLOAD_NAME")
    provider_name = must_env("OBO_PROVIDER_NAME")

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
    print("  Summary - step 03: OBO credential provider")
    print("=" * 70)
    print("  What was created / updated on AgentCore identity:")
    print(f"    Provider name     : {provider_name}")
    print(f"    Provider ARN      : {arn}")
    print("    Vendor            : CustomOauth2")
    print("    Client auth       : PRIVATE_KEY_JWT (same KMS key as M2M)")
    print(f"    Client ID         : {client_id}")
    print(f"    KMS key           : {key_arn}")
    print(f"    JWK kid header    : {kid}")
    print(f"    Discovery URL     : {discovery_url(domain, auth_server_id)}")
    print("    Grant type used   : urn:ietf:params:oauth:grant-type:token-exchange")
    print("    actorTokenContent : NONE  (no actor_token → no RFC 8693 'act' claim)")
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
    print("    the RFC 8693 exchange. The subject_token is a user JWT; Okta")
    print("    returns a new access token whose 'sub' is the user but whose")
    print("    'cid' is your service app - the shape that makes 'call this")
    print("    downstream API on the user's behalf' work cleanly.")
    print()
    print("  Next steps:")
    print("    M2M-only path: run the M2M demo now.")
    print("      python outbound_private_key_jwt_m2m.py")
    print()
    print("    OBO path: continue setup, then run the OBO demo.")
    print("      python setup/03b_create_provider_client.py")
    print("      python get_okta_user_jwt.py")
    print("      python outbound_private_key_jwt_obo.py")


if __name__ == "__main__":
    main()
