#!/usr/bin/env python3
"""
Create the AgentCore Identity OAuth2 credential provider for the OBO_Gateway.

Per design §7d: the OBO path uses an AgentCore Identity credential provider of
vendor `CustomOauth2` configured against the Okta authorization server's
discovery URL. The provider declares (a) basic OAuth2 client credentials and
(b) the on-behalf-of TOKEN_EXCHANGE grant configuration. The grant type is
then RE-DECLARED at gateway-target-create time (9.4 / 04_create_obo_gateway.py)
to actually request it on a given invocation; the provider is grant-agnostic
beyond declaring which grants it CAN execute.

Two providers exist in the deployed system:
  - lakehouse-mcp-okta-oauth-provider: used by Interceptor_Gateway
    target for the gateway -> Claims runtime M2M leg (client_credentials).
    Authenticates as the user-login app (okta-app-client-*).
  - lakehouse-obo-okta-provider       (this script): used by OBO_Gateway target
    for user identity propagation via TOKEN_EXCHANGE (RFC 8693). Authenticates
    as the dedicated OBO exchange app (okta-obo-client-*).

Both reference the SAME Okta authorization server / discovery URL (single
Okta_Authorization_Server per R1.6), but DIFFERENT client apps: the OBO leg
requires a dedicated exchange client distinct from the subject-token issuer
(R1.6 revised: one auth server, two apps).

Idempotent: try-create -> on AlreadyExistsException, list-by-name and reuse
the existing provider's ARN. Mirrors the interceptor side's
create_oauth_provider() shape from 5a-gateway-setup/create_gateway.py.

Per botocore probe of CreateOAuth2CredentialProvider, the OBO config
envelope is:

    customOauth2ProviderConfig.onBehalfOfTokenExchangeConfig:
        grantType: 'TOKEN_EXCHANGE'
        tokenExchangeGrantTypeConfig.actorTokenContent: 'NONE'

This shape was empirically validated — the credential provider reached READY
status against this exact envelope on the operator's Okta tenant.

SSM keys:
  Reads:  okta-discovery-url, okta-obo-client-id, okta-obo-client-secret
  Writes: obo-credential-provider-arn

NOTE (two-client OBO): this provider authenticates as the DEDICATED
OBO exchange app (okta-obo-client-*), NOT the user-login app (okta-app-client-*).
Okta's RFC 8693 TOKEN_EXCHANGE requires the exchanging client to differ from
the subject-token issuer; using okta-app-client-* here fails the exchange with
`unsupported_token_exchange_flow`. The interceptor-side provider
(lakehouse-mcp-okta-oauth-provider) still uses okta-app-client-* for its M2M
client_credentials leg — do not conflate the two.

Usage:
    python 03_create_oauth_provider.py
"""

import sys

import boto3

PROVIDER_NAME = "lakehouse-obo-okta-provider"  # design §7d code snippet
SSM_PREFIX = "/app/lakehouse-agent/"


class SSMConfig:
    """Load Okta config from SSM Parameter Store."""

    def __init__(self):
        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.sts = boto3.client("sts", region_name=self.region)
        self.account_id = self.sts.get_caller_identity()["Account"]

        print("✅ Using AWS configuration")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

        print("\n🔍 Loading configuration from SSM Parameter Store...")
        self.okta_discovery_url = self._get(f"{SSM_PREFIX}okta-discovery-url")
        # OBO exchange-client credentials (dedicated service app, distinct from
        # the user-login app's okta-app-client-* — see module docstring).
        self.okta_obo_client_id = self._get(f"{SSM_PREFIX}okta-obo-client-id")
        self.okta_obo_client_secret = self._get(f"{SSM_PREFIX}okta-obo-client-secret", secure=True)

        print(f"   ✅ Okta Discovery URL: {self.okta_discovery_url}")
        print(f"   ✅ Okta OBO Client ID: {self.okta_obo_client_id}")
        print("   ✅ Okta OBO Client Secret: ****** (loaded)")

    def _get(self, name: str, secure: bool = False) -> str:
        try:
            response = self.ssm.get_parameter(Name=name, WithDecryption=secure)
            return response["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            print(f"❌ SSM parameter {name} not found")
            print("   Please run the setup scripts first (notebook 01)")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error retrieving parameter {name}: {e}")
            sys.exit(1)

    def store_provider_arn(self, provider_arn: str):
        """Persist OBO credential-provider ARN to SSM."""
        print("\n💾 Storing OBO credential-provider ARN in SSM Parameter Store...")
        try:
            self.ssm.put_parameter(
                Name=f"{SSM_PREFIX}obo-credential-provider-arn",
                Value=provider_arn,
                Description="AgentCore Identity OBO credential provider ARN "
                "(CustomOauth2 vendor, TOKEN_EXCHANGE grant)",
                Type="String",
                Overwrite=True,
            )
            print(f"✅ Stored parameter: {SSM_PREFIX}obo-credential-provider-arn = {provider_arn}")
        except Exception as e:
            print(f"❌ Error storing parameter: {e}")
            raise


def find_existing_provider_arn(client, name: str):
    """Look up an existing provider's ARN by name. Returns None if not found."""
    try:
        response = client.list_oauth2_credential_providers()
    except Exception as e:
        print(f"   ⚠️  list_oauth2_credential_providers failed: {e}")
        return None

    providers = (
        response.get("credentialProviders") or response.get("oauth2CredentialProviders") or response.get("items") or []
    )
    for p in providers:
        if p.get("name") == name:
            arn = p.get("credentialProviderArn") or p.get("oauth2CredentialProviderArn") or p.get("arn")
            if arn:
                return arn
    return None


def create_obo_provider(client, config: SSMConfig) -> str:
    """
    Create (or reuse) the OBO credential provider.

    Returns:
        Provider ARN.
    """
    print(f"\n🔐 Creating OAuth2 credential provider: {PROVIDER_NAME}")
    print("   Vendor: CustomOauth2")
    print(f"   Discovery URL: {config.okta_discovery_url}")
    print("   On-behalf-of grant: TOKEN_EXCHANGE (RFC 8693)")

    # The OBO grant is declared on the provider under
    # customOauth2ProviderConfig.onBehalfOfTokenExchangeConfig.
    # actorTokenContent='NONE' matches the working configuration (no actor
    # token; the user JWT alone drives the exchange).
    provider_config = {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {
                "discoveryUrl": config.okta_discovery_url,
            },
            "clientId": config.okta_obo_client_id,
            "clientSecret": config.okta_obo_client_secret,
            "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
            "onBehalfOfTokenExchangeConfig": {
                "grantType": "TOKEN_EXCHANGE",
                "tokenExchangeGrantTypeConfig": {
                    "actorTokenContent": "NONE",
                },
            },
        }
    }

    try:
        response = client.create_oauth2_credential_provider(
            name=PROVIDER_NAME,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=provider_config,
            tags={"Application": "lakehouse-agent", "Purpose": "obo-okta-provider"},
        )
        provider_arn = (
            response.get("credentialProviderArn") or response.get("oauth2CredentialProviderArn") or response.get("arn")
        )
        if not provider_arn:
            print(f"   Debug - response keys: {list(response.keys())}")
            raise KeyError(
                f"create_oauth2_credential_provider response did not contain "
                f"a recognizable ARN field. Available keys: {list(response.keys())}"
            )
        print(f"✅ Created OBO credential provider: {provider_arn}")
        return provider_arn

    except Exception as e:
        msg = str(e)
        if "already exists" in msg.lower() or "AlreadyExistsException" in msg:
            print(f"ℹ️  Provider {PROVIDER_NAME} already exists; retrieving ARN...")
            arn = find_existing_provider_arn(client, PROVIDER_NAME)
            if arn:
                print(f"✅ Using existing provider: {arn}")
                return arn
            print(f"❌ Provider {PROVIDER_NAME} reported existing but not in list")
            raise
        print(f"❌ Error creating OAuth2 provider: {e}")
        raise


def main():
    print("=" * 70)
    print("OBO Gateway: OAuth2 Credential Provider")
    print("=" * 70)

    config = SSMConfig()

    client = boto3.client("bedrock-agentcore-control", region_name=config.region)

    try:
        provider_arn = create_obo_provider(client, config)
        config.store_provider_arn(provider_arn)

        print("\n" + "=" * 70)
        print("OBO Credential Provider Setup Complete!")
        print("=" * 70)
        print(f"\n✅ Provider: {PROVIDER_NAME}")
        print(f"   ARN: {provider_arn}")
        print("\n📋 Next Steps:")
        print("   1. Run 04_create_obo_gateway.py to create the OBO_Gateway")
        print("      and target referencing this provider.")
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ OBO credential-provider setup failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
