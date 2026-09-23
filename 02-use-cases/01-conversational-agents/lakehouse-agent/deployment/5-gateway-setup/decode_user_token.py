#!/usr/bin/env python3
"""
Decode User JWT Token and Check Gateway Configuration

This script:
1. Authenticates a user and gets their JWT token
2. Decodes and displays all token claims
3. Checks Gateway JWT authorizer configuration
4. Compares token claims with Gateway expectations

Usage:
    python decode_user_token.py --username <username> --password <password>
"""

import boto3
import json
import base64
import argparse
import sys


def decode_jwt(token):
    """Decode JWT token without verification."""
    parts = token.split(".")
    if len(parts) != 3:
        return None, None

    # Decode header
    header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))

    # Decode payload
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))

    return header, payload


def get_user_tokens(username, password):
    """Get user tokens from Cognito."""
    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    print("=" * 70)
    print("Step 1: Authenticate User and Get Tokens")
    print("=" * 70)

    # Get Cognito configuration
    print("\n📋 Loading Cognito configuration...")
    client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-id")["Parameter"]["Value"]
    client_secret = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-secret", WithDecryption=True)[
        "Parameter"
    ]["Value"]
    user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]

    print(f"   Client ID: {client_id}")
    print(f"   User Pool: {user_pool_id}")

    # Authenticate user
    print(f"\n🔐 Authenticating user: {username}")

    import hmac
    import hashlib

    message = username + client_id
    secret_hash = base64.b64encode(hmac.new(client_secret.encode(), message.encode(), hashlib.sha256).digest()).decode()

    try:
        response = cognito.admin_initiate_auth(
            UserPoolId=user_pool_id,
            ClientId=client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
                "SECRET_HASH": secret_hash,
            },
        )

        access_token = response["AuthenticationResult"]["AccessToken"]
        id_token = response["AuthenticationResult"]["IdToken"]

        print("✅ User authenticated successfully!")

        return access_token, id_token, region, client_id, user_pool_id

    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return None, None, None, None, None


def check_gateway_config(region):
    """Check Gateway JWT authorizer configuration."""
    print("\n" + "=" * 70)
    print("Step 2: Check Gateway JWT Authorizer Configuration")
    print("=" * 70)

    ssm = boto3.client("ssm", region_name=region)
    agentcore = boto3.client("bedrock-agentcore-control", region_name=region)

    try:
        gateway_id = ssm.get_parameter(Name="/app/lakehouse-agent/gateway-id")["Parameter"]["Value"]
        print(f"\n📦 Gateway ID: {gateway_id}")

        gateway_details = agentcore.get_gateway(gatewayIdentifier=gateway_id)
        auth_config = gateway_details.get("authorizerConfiguration", {})

        if "customJWTAuthorizer" in auth_config:
            jwt_config = auth_config["customJWTAuthorizer"]
            print("\n🔐 JWT Authorizer Configuration:")
            print(f"   Discovery URL: {jwt_config.get('discoveryUrl', 'N/A')}")
            print(f"   Allowed Audience: {jwt_config.get('allowedAudience', [])}")
            print(f"   Allowed Clients: {jwt_config.get('allowedClients', [])}")
            return jwt_config
        else:
            print("\n⚠️  No JWT authorizer configured")
            return None

    except Exception as e:
        print(f"\n⚠️  Could not get Gateway configuration: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Decode user JWT token and check Gateway config")
    parser.add_argument("--username", required=True, help="Cognito username")
    parser.add_argument("--password", required=True, help="User password")
    args = parser.parse_args()

    # Get user tokens
    access_token, id_token, region, client_id, user_pool_id = get_user_tokens(args.username, args.password)

    if not access_token:
        sys.exit(1)

    # Decode tokens
    print("\n" + "=" * 70)
    print("Step 3: Decode and Inspect Tokens")
    print("=" * 70)

    print("\n📄 ID TOKEN:")
    print("=" * 70)
    id_header, id_payload = decode_jwt(id_token)
    print(json.dumps(id_payload, indent=2))

    print("\n📄 ACCESS TOKEN:")
    print("=" * 70)
    access_header, access_payload = decode_jwt(access_token)
    print(json.dumps(access_payload, indent=2))

    # Check Gateway configuration
    gateway_config = check_gateway_config(region)

    # Compare token with Gateway expectations
    print("\n" + "=" * 70)
    print("Step 4: Validate Token Against Gateway Configuration")
    print("=" * 70)

    if gateway_config:
        print("\n🔍 Checking token compatibility...")

        # Check issuer
        expected_issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        token_issuer = access_payload.get("iss", "")

        print("\n1. Issuer (iss):")
        print(f"   Expected: {expected_issuer}")
        print(f"   Token:    {token_issuer}")
        if token_issuer == expected_issuer:
            print("   ✅ Match")
        else:
            print("   ❌ Mismatch")

        # Check client_id
        allowed_clients = gateway_config.get("allowedClients", [])
        token_client_id = access_payload.get("client_id", "")

        print("\n2. Client ID:")
        print(f"   Allowed: {allowed_clients}")
        print(f"   Token:   {token_client_id}")
        if token_client_id in allowed_clients:
            print("   ✅ Match")
        else:
            print("   ❌ Not in allowed clients")

        # Check audience (if configured)
        allowed_audience = gateway_config.get("allowedAudience", [])
        token_aud = access_payload.get("aud", "")

        print("\n3. Audience (aud):")
        print(f"   Allowed: {allowed_audience}")
        print(f"   Token:   {token_aud}")
        if not allowed_audience:
            print("   ℹ️  No audience restriction configured")
        elif token_aud in allowed_audience:
            print("   ✅ Match")
        else:
            print("   ❌ Not in allowed audience")

        # Check token_use
        token_use = access_payload.get("token_use", "")
        print("\n4. Token Use:")
        print(f"   Token: {token_use}")
        if token_use == "access":
            print("   ✅ Correct (should be 'access' for API calls)")
        else:
            print("   ⚠️  Unexpected token_use value")

        # Summary
        print("\n" + "=" * 70)
        print("Summary")
        print("=" * 70)

        issues = []
        if token_issuer != expected_issuer:
            issues.append("❌ Issuer mismatch")
        if token_client_id not in allowed_clients:
            issues.append("❌ Client ID not in allowed clients")
        if allowed_audience and token_aud not in allowed_audience:
            issues.append("❌ Audience not in allowed audience")

        if issues:
            print("\n❌ Token validation issues found:")
            for issue in issues:
                print(f"   {issue}")
            print("\n💡 Possible solutions:")
            print("   1. Redeploy Gateway with correct client ID in allowedClients")
            print("   2. Check that user authenticated with correct Cognito client")
            print("   3. Verify Gateway JWT authorizer configuration")
        else:
            print("\n✅ Token should be accepted by Gateway!")
            print("   All claims match Gateway configuration")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
