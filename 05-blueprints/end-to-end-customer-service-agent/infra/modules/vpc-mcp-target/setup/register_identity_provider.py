"""
Register an Entra ID identity provider with AgentCore for delegated access.

This stores the gateway client app credentials in AgentCore Identity and
configures the delegation grant so the gateway can exchange inbound user
tokens for downstream-scoped tokens automatically.

Run once per environment. Requires the app client secret from Azure portal.

Usage:
    python register_identity_provider.py --client-secret <value>
"""

import argparse
import os
import boto3

TENANT = os.environ.get("ENTRA_TENANT_ID", "<your-tenant-id>")
CLIENT_ID = os.environ.get("ENTRA_AGENT_CLIENT_ID", "<your-gateway-client-id>")
REGION = os.environ.get("AWS_REGION", "us-east-1")
PROVIDER_NAME = "cx-entra-delegated-access"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-secret", required=True, help="App registration client secret")
    args = parser.parse_args()

    ac = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print(f"Registering identity provider: {PROVIDER_NAME}")

    try:
        resp = ac.create_oauth2_credential_provider(
            name=PROVIDER_NAME,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {
                        "discoveryUrl": f"https://login.microsoftonline.com/{TENANT}/v2.0/.well-known/openid-configuration"
                    },
                    "clientId": CLIENT_ID,
                    "clientSecret": args.client_secret,
                    "onBehalfOfTokenExchangeConfig": {
                        "grantType": "JWT_AUTHORIZATION_GRANT"
                    },
                }
            },
        )
        print(f"  ARN:    {resp['credentialProviderArn']}")
        print(f"  Status: {resp['status']}")
    except ac.exceptions.ConflictException:
        resp = ac.get_oauth2_credential_provider(name=PROVIDER_NAME)
        print(f"  Already exists: {resp['credentialProviderArn']}")


if __name__ == "__main__":
    main()
