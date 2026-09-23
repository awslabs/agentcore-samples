#!/usr/bin/env python3
"""
Decode JWT Token to inspect claims

This script gets a token from the M2M client and decodes it to see what's inside.
"""

import boto3
import json
import base64
import requests


def decode_jwt(token):
    """Decode JWT token without verification (for inspection only)."""
    parts = token.split(".")
    if len(parts) != 3:
        print("Invalid JWT token format")
        return None

    # Decode header
    header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))

    # Decode payload
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))

    return header, payload


def main():
    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)

    print("=" * 70)
    print("Decode M2M JWT Token")
    print("=" * 70)

    # Get M2M client credentials
    try:
        client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-m2m-client-id")["Parameter"]["Value"]
        client_secret = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-m2m-client-secret", WithDecryption=True)[
            "Parameter"
        ]["Value"]
        domain = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-domain")["Parameter"]["Value"]
        user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]

        print("\n✅ Configuration loaded:")
        print(f"   Client ID: {client_id}")
        print(f"   Domain: {domain}")
        print(f"   User Pool ID: {user_pool_id}")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return

    # Get token
    print("\n🔐 Requesting token...")
    token_endpoint = f"{domain}/oauth2/token"
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    # Get the configured scopes
    cognito = boto3.client("cognito-idp", region_name=region)
    client_details = cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
    allowed_scopes = client_details["UserPoolClient"].get("AllowedOAuthScopes", [])
    scope_string = " ".join(allowed_scopes)

    response = requests.post(
        token_endpoint,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}",
        },
        data={"grant_type": "client_credentials", "scope": scope_string},
    )

    if response.status_code != 200:
        print(f"❌ Token request failed: {response.status_code}")
        print(f"   Response: {response.text}")
        return

    token_data = response.json()
    access_token = token_data["access_token"]

    print("✅ Token received")

    # Decode token
    print("\n📋 Decoding JWT Token...")
    header, payload = decode_jwt(access_token)

    print("\n🔑 JWT Header:")
    print(json.dumps(header, indent=2))

    print("\n📦 JWT Payload:")
    print(json.dumps(payload, indent=2))

    # Check important claims
    print("\n🔍 Important Claims:")
    print(f"   Issuer (iss): {payload.get('iss', 'N/A')}")
    print(f"   Client ID (client_id): {payload.get('client_id', 'N/A')}")
    print(f"   Audience (aud): {payload.get('aud', 'N/A')}")
    print(f"   Scope: {payload.get('scope', 'N/A')}")
    print(f"   Token Use: {payload.get('token_use', 'N/A')}")
    print(f"   Expires (exp): {payload.get('exp', 'N/A')}")

    # Check if issuer matches expected format
    expected_issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    if payload.get("iss") == expected_issuer:
        print("\n   ✅ Issuer matches expected format")
    else:
        print("\n   ⚠️  Issuer mismatch!")
        print(f"      Expected: {expected_issuer}")
        print(f"      Got: {payload.get('iss')}")

    # Check client_id
    if payload.get("client_id") == client_id:
        print("   ✅ Client ID matches")
    else:
        print("   ⚠️  Client ID mismatch!")
        print(f"      Expected: {client_id}")
        print(f"      Got: {payload.get('client_id')}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
