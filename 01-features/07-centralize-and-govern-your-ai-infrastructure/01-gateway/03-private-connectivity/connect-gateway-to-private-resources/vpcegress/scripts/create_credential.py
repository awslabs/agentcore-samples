"""Create a credential provider (OAuth2 or API Key) for outbound auth.

Usage:
    # OAuth2 credential provider
    python scripts/create_credential.py \
      --name my-oauth-credential \
      --type oauth \
      --discovery-url https://cognito-idp.us-west-2.amazonaws.com/us-west-2_xxx/.well-known/openid-configuration \
      --client-id xxx \
      --client-secret xxx

    # API Key credential provider
    python scripts/create_credential.py \
      --name my-apikey-credential \
      --type api-key \
      --api-key-value sk-xxx \
      --header-name x-api-key
"""

import argparse

import boto3
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser(description="Create a credential provider")
    parser.add_argument("--name", required=True, help="Credential provider name")
    parser.add_argument(
        "--type",
        required=True,
        choices=["oauth", "api-key"],
        help="Credential type: oauth or api-key",
    )

    # OAuth args
    parser.add_argument("--discovery-url", help="OIDC discovery URL (for oauth type)")
    parser.add_argument("--client-id", help="OAuth client ID (for oauth type)")
    parser.add_argument("--client-secret", help="OAuth client secret (for oauth type)")

    # API Key args
    parser.add_argument("--api-key-value", help="API key value (for api-key type)")
    parser.add_argument(
        "--header-name",
        default="x-api-key",
        help="Header name for API key (default: x-api-key)",
    )

    args = parser.parse_args()

    region = boto3.Session().region_name
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    print(f"--- Creating {args.type} credential provider '{args.name}' ---")

    if args.type == "oauth":
        if not all([args.discovery_url, args.client_id, args.client_secret]):
            print(
                "ERROR: --discovery-url, --client-id, --client-secret required for oauth type"
            )
            raise SystemExit(1)

        try:
            resp = control.create_oauth2_credential_provider(
                name=args.name,
                credentialProviderVendor="CustomOauth2",
                oauth2ProviderConfigInput={
                    "customOauth2ProviderConfig": {
                        "oauthDiscovery": {"discoveryUrl": args.discovery_url},
                        "clientId": args.client_id,
                        "clientSecret": args.client_secret,
                    }
                },
            )
            cred_arn = resp["credentialProviderArn"]
            print(f"  Created: {cred_arn}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConflictException":
                cred_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default/oauth2credentialprovider/{args.name}"
                print(f"  Already exists: {cred_arn}")
            else:
                raise

    elif args.type == "api-key":
        if not args.api_key_value:
            print("ERROR: --api-key-value required for api-key type")
            raise SystemExit(1)

        try:
            resp = control.create_api_key_credential_provider(
                name=args.name,
                apiKey=args.api_key_value,
            )
            cred_arn = resp["credentialProviderArn"]
            print(f"  Created: {cred_arn}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConflictException":
                cred_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default/apikeycredentialprovider/{args.name}"
                print(f"  Already exists: {cred_arn}")
            else:
                raise


if __name__ == "__main__":
    main()
