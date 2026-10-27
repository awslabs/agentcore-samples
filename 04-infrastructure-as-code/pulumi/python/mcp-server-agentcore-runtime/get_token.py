#!/usr/bin/env python3
"""
Simple Cognito Authentication Script
Matches the approach from the original tutorial
"""

import boto3
import sys


def get_token(client_id, username, password, region=None):
    """Get authentication token from Cognito."""
    # Use provided region or default from environment/config
    if region:
        cognito_client = boto3.client("cognito-idp", region_name=region)
    else:
        cognito_client = boto3.client("cognito-idp")

    try:
        auth_response = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )

        return auth_response["AuthenticationResult"]["AccessToken"]

    except Exception as e:
        print(f"Error: {e}")
        print("Troubleshooting:")
        print("  - Verify the Client ID is correct")
        print("  - Ensure you're using the correct region")
        print("  - Check that the user exists and password is correct")
        print("  - Verify USER_PASSWORD_AUTH is enabled for this client")
        sys.exit(1)


def main():
    if len(sys.argv) < 4 or len(sys.argv) > 5:
        print("Usage: python get_token.py <client_id> <username> <password> [region]")
        sys.exit(1)

    client_id = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    region = sys.argv[4] if len(sys.argv) == 5 else None

    if region:
        print(f"Authenticating with Cognito in region {region}...")
    else:
        print("Authenticating with Cognito...")

    token = get_token(client_id, username, password, region)

    print("\n" + "=" * 70)
    print("Authentication Successful!")
    print("=" * 70)
    print("\nAccess Token:")
    print(token)
    print("\n" + "=" * 70)
    print("Export Command:")
    print("=" * 70)
    print(f'\nexport JWT_TOKEN="{token}"')
    print("\nThen use in curl:")
    print('curl -H "Authorization: Bearer $JWT_TOKEN" <your-api-url>')
    print()


if __name__ == "__main__":
    main()
