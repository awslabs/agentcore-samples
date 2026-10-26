"""Create the outbound OAuth credential provider for the Databricks App target.

Databricks Apps enforce Databricks OAuth Bearer auth. The gateway authenticates
to the App as a Databricks service principal using OAuth2 client credentials.
This creates a CustomOauth2 credential provider pointed at the workspace OIDC
metadata; the target then uses grantType=CLIENT_CREDENTIALS to mint a Databricks
token (scope all-apis) and attach it outbound.

No Databricks user federation or SCIM sync is needed: this is service-principal
(machine-to-machine) trust, independent of the inbound Entra identity.

Writes CREDENTIAL_PROVIDER_ARN to the script-local .env.

Usage:
    uv run python scripts/databricks-a2a-target/deploy_credential.py \
      --name databricks-a2a-oauth \
      --workspace-host dbc-xxxxxxxx-xxxx.cloud.databricks.com \
      --client-id $DATABRICKS_SP_CLIENT_ID \
      --client-secret $DATABRICKS_SP_CLIENT_SECRET
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
        description="Create a Databricks service-principal OAuth credential provider"
    )
    parser.add_argument("--name", default="databricks-a2a-oauth", help="Provider name")
    parser.add_argument(
        "--workspace-host",
        required=True,
        help="Databricks workspace host (no scheme), e.g. dbc-xxxx.cloud.databricks.com",
    )
    parser.add_argument(
        "--client-id", required=True, help="Databricks service principal application ID"
    )
    parser.add_argument(
        "--client-secret",
        required=True,
        help="Databricks service principal OAuth secret",
    )
    args = parser.parse_args()

    host = args.workspace_host.replace("https://", "").rstrip("/")
    # Databricks publishes OAuth/OIDC metadata at the workspace OIDC path; the
    # token endpoint it advertises is https://<host>/oidc/v1/token. AgentCore
    # requires the discovery URL to end with .well-known/openid-configuration,
    # which Databricks also serves (same token endpoint, client_credentials).
    discovery_url = f"https://{host}/oidc/.well-known/openid-configuration"

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    print(f"--- Creating Databricks OAuth credential provider '{args.name}' ---")
    resp = admin.client.create_oauth2_credential_provider(
        name=args.name,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput={
            "customOauth2ProviderConfig": {
                "oauthDiscovery": {"discoveryUrl": discovery_url},
                "clientId": args.client_id,
                "clientSecret": args.client_secret,
                # Databricks accepts HTTP Basic client auth at its token
                # endpoint (the same scheme as `curl -u client_id:secret`).
                "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
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
