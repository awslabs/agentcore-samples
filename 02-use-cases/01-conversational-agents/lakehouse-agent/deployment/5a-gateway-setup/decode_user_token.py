#!/usr/bin/env python3
"""
Decode User JWT Token and Check Gateway Configuration

Works on both IdPs (IDP_PROVIDER = "cognito" | "okta"). This script:
1. Obtains a user JWT — either pasted via --token, or (Cognito only) minted from
   --username/--password
2. Decodes and displays the token claims (base64url, no signature verification)
3. Checks the Gateway JWT authorizer configuration
4. Compares the token claims against what the Gateway expects

Okta Identity Engine tenants block the Resource Owner Password Credentials
(ROPC) grant, so --username/--password cannot mint a token on the Okta path.
There, sign in through the Streamlit UI (notebook 08, Authorization Code +
PKCE), copy the access token, and paste it with --token.

Usage:
    # Both IdPs — paste a token you already hold
    python decode_user_token.py --token <jwt>

    # Cognito only — mint a token from test-user credentials
    python decode_user_token.py --username <username> --password <password>
"""

import argparse
import base64
import json
import os
import sys

import boto3

# Make the repo's utils/ importable (idp_config lives there) when this script
# runs from its own deployment subdir.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.idp_config import get_idp_provider

# Fail-fast guidance shown when a reader tries the Cognito credential path on Okta.
OKTA_ROPC_HELP = (
    "❌ --username/--password is a Cognito-only convenience path, but IDP_PROVIDER='okta'.\n"
    "   Okta Identity Engine tenants block the Resource Owner Password Credentials (ROPC)\n"
    "   grant, so a username + password cannot mint a token here.\n"
    "   ➜ Sign in through the Streamlit UI (notebook 08, Authorization Code + PKCE), copy\n"
    "     the access token, then re-run:\n"
    "         python decode_user_token.py --token <jwt>"
)


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


def get_user_tokens(ssm, region, username, password):
    """Get user tokens from Cognito (Cognito path only)."""
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

    import hashlib
    import hmac

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

        return access_token, id_token

    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return None, None


def resolve_expected_issuer(ssm, region, idp_provider):
    """Resolve the issuer the Gateway expects, from the active IdP's SSM contract."""
    try:
        if idp_provider == "cognito":
            user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]
            return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"

        # [OKTA] custom authorization server issuer
        org_url = ssm.get_parameter(Name="/app/lakehouse-agent/okta-org-url")["Parameter"]["Value"]
        auth_server_id = ssm.get_parameter(Name="/app/lakehouse-agent/okta-auth-server-id")["Parameter"]["Value"]
        return f"https://{org_url}/oauth2/{auth_server_id}"
    except Exception as e:
        print(f"\n⚠️  Could not resolve the expected issuer from SSM: {e}")
        return None


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


def validate_cognito_token(gateway_config, access_payload, expected_issuer):
    """Compare a Cognito access token's claims against the Gateway authorizer config."""
    print("\n🔍 Checking token compatibility...")

    # Check issuer
    token_issuer = access_payload.get("iss", "")

    print("\n1. Issuer (iss):")
    print(f"   Expected: {expected_issuer or '(could not resolve)'}")
    print(f"   Token:    {token_issuer}")
    issuer_match = bool(expected_issuer) and token_issuer == expected_issuer
    if issuer_match:
        print("   ✅ Match")
    else:
        print("   ❌ Mismatch")

    # Check client_id
    allowed_clients = gateway_config.get("allowedClients", [])
    token_client_id = access_payload.get("client_id", "")

    print("\n2. Client ID:")
    print(f"   Allowed: {allowed_clients}")
    print(f"   Token:   {token_client_id}")
    client_match = token_client_id in allowed_clients
    if client_match:
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

    issues = []
    if not issuer_match:
        issues.append("❌ Issuer mismatch")
    if not client_match:
        issues.append("❌ Client ID not in allowed clients")
    if allowed_audience and token_aud not in allowed_audience:
        issues.append("❌ Audience not in allowed audience")

    solutions = [
        "1. Redeploy Gateway with correct client ID in allowedClients",
        "2. Check that user authenticated with correct Cognito client",
        "3. Verify Gateway JWT authorizer configuration",
    ]
    return issues, solutions


def validate_okta_token(gateway_config, payload, expected_issuer):
    """Compare an Okta access token's claims against the Gateway authorizer config."""
    print("\n🔍 Checking token compatibility...")

    # Check issuer
    token_issuer = payload.get("iss", "")

    print("\n1. Issuer (iss):")
    print(f"   Expected: {expected_issuer or '(could not resolve)'}")
    print(f"   Token:    {token_issuer}")
    issuer_match = bool(expected_issuer) and token_issuer == expected_issuer
    if issuer_match:
        print("   ✅ Match")
    else:
        print("   ❌ Mismatch")

    # Check audience. Okta access tokens carry `aud` as a string; the Gateway
    # authorizer holds allowedAudience as a list.
    allowed_audience = gateway_config.get("allowedAudience", [])
    token_aud = payload.get("aud", "")
    aud_match = (token_aud in allowed_audience) if allowed_audience else False

    print("\n2. Audience (aud):")
    print(f"   Allowed: {allowed_audience}")
    print(f"   Token:   {token_aud}")
    if not allowed_audience:
        print("   ℹ️  No audience restriction configured")
    elif aud_match:
        print("   ✅ Match")
    else:
        print("   ❌ Not in allowed audience")

    # Okta access tokens name the minting client in `cid` (informational).
    print("\n3. Client ID (cid):")
    print(f"   Token: {payload.get('cid', 'N/A')}")
    print("   ℹ️  Informational — the Okta authorizer validates `aud`, not `allowedClients`")

    issues = []
    if not issuer_match:
        issues.append("❌ Issuer mismatch")
    if allowed_audience and not aud_match:
        issues.append("❌ Audience not in allowed audience")

    solutions = [
        "1. Confirm the token was minted from the Okta authorization server in SSM",
        "2. Verify the Gateway was deployed against the current SSM Okta config",
        "3. Re-run: python deployment/5a-gateway-setup/create_gateway.py",
    ]
    return issues, solutions


def main():
    parser = argparse.ArgumentParser(
        description="Decode a user JWT token and check the Gateway JWT authorizer config (Cognito or Okta)",
        epilog=(
            "--token works on BOTH IdPs. --username/--password is a Cognito-only convenience "
            "path: Okta Identity Engine tenants block the ROPC grant, so use --token there "
            "with a token from the Streamlit UI login (notebook 08)."
        ),
    )
    parser.add_argument(
        "--token",
        help="User JWT to inspect (works on both IdPs; paste a token you already hold)",
    )
    parser.add_argument(
        "--username",
        help="Cognito username (Cognito only; mints a token via ADMIN_USER_PASSWORD_AUTH)",
    )
    parser.add_argument(
        "--password",
        help="Cognito user password (Cognito only; used together with --username)",
    )
    args = parser.parse_args()

    # --token and --username/--password are mutually exclusive token sources.
    if args.token and (args.username or args.password):
        parser.error("--token cannot be combined with --username/--password — supply one token source, not both.")
    if not args.token and not (args.username and args.password):
        parser.error(
            "No token source supplied. Either paste a token with --token <jwt> (works on Cognito "
            "and Okta), or supply BOTH --username and --password (Cognito only)."
        )

    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)
    idp_provider = get_idp_provider(ssm)
    print(f"\n📍 IDP_PROVIDER: {idp_provider}")

    if args.token:
        print("\n" + "=" * 70)
        print("Step 1: Decode and Inspect Pasted Token")
        print("=" * 70)

        header, subject_payload = decode_jwt(args.token)
        if header is None:
            print("❌ Token does not look like a JWT (expected three '.'-separated parts)")
            sys.exit(1)

        print("\n📄 HEADER:")
        print(json.dumps(header, indent=2))

        print("\n📄 PAYLOAD:")
        print(json.dumps(subject_payload, indent=2))
    else:
        # Cognito-only convenience path.
        if idp_provider != "cognito":
            print(f"\n{OKTA_ROPC_HELP}")
            sys.exit(1)

        access_token, id_token = get_user_tokens(ssm, region, args.username, args.password)
        if not access_token:
            sys.exit(1)

        print("\n" + "=" * 70)
        print("Step 1b: Decode and Inspect Tokens")
        print("=" * 70)

        print("\n📄 ID TOKEN:")
        print("=" * 70)
        _id_header, id_payload = decode_jwt(id_token)
        print(json.dumps(id_payload, indent=2))

        print("\n📄 ACCESS TOKEN:")
        print("=" * 70)
        _access_header, subject_payload = decode_jwt(access_token)
        print(json.dumps(subject_payload, indent=2))

    # Check Gateway configuration
    gateway_config = check_gateway_config(region)

    # Compare token with Gateway expectations
    print("\n" + "=" * 70)
    print("Step 3: Validate Token Against Gateway Configuration")
    print("=" * 70)

    if not gateway_config:
        print("\n⚠️  Skipping comparison — no Gateway JWT authorizer config retrieved")
        print("\n" + "=" * 70)
        return

    expected_issuer = resolve_expected_issuer(ssm, region, idp_provider)

    if idp_provider == "cognito":
        issues, solutions = validate_cognito_token(gateway_config, subject_payload, expected_issuer)
    else:
        issues, solutions = validate_okta_token(gateway_config, subject_payload, expected_issuer)

    # Summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)

    if issues:
        print("\n❌ Token validation issues found:")
        for issue in issues:
            print(f"   {issue}")
        print("\n💡 Possible solutions:")
        for solution in solutions:
            print(f"   {solution}")
    else:
        print("\n✅ Token should be accepted by Gateway!")
        print("   All claims match Gateway configuration")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
