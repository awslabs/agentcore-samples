"""Delete a credential provider by name and type.

Usage:
    python scripts/delete_credential.py --name my-oauth-credential --type oauth
    python scripts/delete_credential.py --name my-apikey-credential --type api-key
"""

import argparse

import boto3
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser(description="Delete a credential provider")
    parser.add_argument("--name", required=True, help="Credential provider name")
    parser.add_argument(
        "--type",
        required=True,
        choices=["oauth", "api-key"],
        help="Credential type: oauth or api-key",
    )
    args = parser.parse_args()

    region = boto3.Session().region_name
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"--- Deleting {args.type} credential provider '{args.name}' ---")

    try:
        if args.type == "oauth":
            control.delete_oauth2_credential_provider(name=args.name)
        elif args.type == "api-key":
            control.delete_api_key_credential_provider(name=args.name)
        print(f"  Deleted: {args.name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  Not found (already deleted): {args.name}")
        else:
            print(f"  Error: {e}")


if __name__ == "__main__":
    main()
