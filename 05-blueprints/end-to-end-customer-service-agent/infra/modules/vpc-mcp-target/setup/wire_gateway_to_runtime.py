"""
Connect the AgentCore Gateway to the MCP Runtime with delegated auth.

Creates a gateway target pointing to the runtime endpoint. Uses DYNAMIC
tool discovery so the gateway resolves available tools at invocation time
(when a user token is present) rather than during target creation.

The Terraform provider does not yet support the TOKEN_EXCHANGE grant type
or DYNAMIC listing mode, so this step uses the boto3 API directly.

Usage:
    python wire_gateway_to_runtime.py \
        --gateway-id <gateway-identifier> \
        --runtime-id <runtime-identifier> \
        --provider-arn <credential-provider-arn>
"""

import argparse
import os
import boto3

AUDIENCE = os.environ.get("ENTRA_MCP_CLIENT_ID", "<your-mcp-server-client-id>")
REGION = os.environ.get("AWS_REGION", "us-east-1")
ACCOUNT = os.environ.get("AWS_ACCOUNT_ID", "<your-aws-account-id>")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-id", required=True)
    parser.add_argument("--runtime-id", required=True)
    parser.add_argument("--provider-arn", required=True)
    parser.add_argument("--name", default="cx-private-mcp-tools")
    args = parser.parse_args()

    ac = boto3.client("bedrock-agentcore-control", region_name=REGION)

    endpoint = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com"
        f"/runtimes/{args.runtime_id}/invocations"
        f"?qualifier=DEFAULT&accountId={ACCOUNT}"
    )

    print(f"Wiring gateway → runtime")
    print(f"  Gateway:  {args.gateway_id}")
    print(f"  Runtime:  {args.runtime_id}")
    print(f"  Provider: {args.provider_arn}")
    print(f"  Endpoint: {endpoint}")

    resp = ac.create_gateway_target(
        gatewayIdentifier=args.gateway_id,
        name=args.name,
        targetConfiguration={
            "mcp": {
                "mcpServer": {
                    "endpoint": endpoint,
                    "listingMode": "DYNAMIC",
                }
            }
        },
        credentialProviderConfigurations=[{
            "credentialProviderType": "OAUTH",
            "credentialProvider": {
                "oauthCredentialProvider": {
                    "providerArn": args.provider_arn,
                    "scopes": [f"api://{AUDIENCE}/user_impersonation"],
                    "grantType": "TOKEN_EXCHANGE",
                    "customParameters": {"requested_token_use": "on_behalf_of"},
                }
            },
        }],
    )

    print(f"\n  Target ID: {resp['targetId']}")
    print(f"  Status:    {resp['status']}")


if __name__ == "__main__":
    main()
