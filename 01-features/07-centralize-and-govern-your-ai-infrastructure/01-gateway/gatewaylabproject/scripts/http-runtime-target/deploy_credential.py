"""Create the outbound OBO credential provider for the HTTP runtime target.

The gateway exchanges the caller's inbound Microsoft Entra ID token for a token
scoped to the runtime, on behalf of the user (OBO). This creates a CustomOauth2
credential provider configured for JWT Authorization Grant token exchange.
Writes CREDENTIAL_PROVIDER_ARN to the script-local .env.

Usage:
    uv run python scripts/http-runtime-target/deploy_credential.py \
      --name http-runtime-obo \
      --tenant-id $MICROSOFT_TENANT_ID \
      --client-id $MICROSOFT_GATEWAY_CLIENT_ID \
      --client-secret $MICROSOFT_GATEWAY_CLIENT_SECRET
"""

import argparse
import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client


def save_env(updates):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars.update(updates)
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Create an Entra ID OBO credential provider for outbound auth"
    )
    parser.add_argument("--name", default="http-runtime-obo", help="Provider name")
    parser.add_argument("--tenant-id", required=True, help="Entra ID tenant ID")
    parser.add_argument(
        "--client-id", required=True, help="Gateway app (client) ID from Entra ID"
    )
    parser.add_argument(
        "--client-secret", required=True, help="Gateway app client secret"
    )
    args = parser.parse_args()

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    # OBO exchange uses the Entra v2.0 token endpoint, so the provider's
    # discovery URL uses the v2.0 OIDC document.
    discovery_url = (
        f"https://login.microsoftonline.com/{args.tenant_id}"
        "/v2.0/.well-known/openid-configuration"
    )

    print(f"--- Creating Entra ID OBO credential provider '{args.name}' ---")
    # GatewayBoto3Client.create_credential_provider has no OBO support, so call
    # create_oauth2_credential_provider directly to inject the OBO config.
    resp = admin.client.create_oauth2_credential_provider(
        name=args.name,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput={
            "customOauth2ProviderConfig": {
                "oauthDiscovery": {"discoveryUrl": discovery_url},
                "clientId": args.client_id,
                "clientSecret": args.client_secret,
                "clientAuthenticationMethod": "CLIENT_SECRET_POST",
                "onBehalfOfTokenExchangeConfig": {
                    "grantType": "JWT_AUTHORIZATION_GRANT"
                },
            }
        },
    )
    cred_arn = resp["credentialProviderArn"]
    print(f"  Credential provider ARN: {cred_arn}")

    save_env(
        {"CREDENTIAL_PROVIDER_ARN": cred_arn, "CREDENTIAL_PROVIDER_NAME": args.name}
    )
    print("\n  Saved CREDENTIAL_PROVIDER_ARN to .env")


if __name__ == "__main__":
    main()
