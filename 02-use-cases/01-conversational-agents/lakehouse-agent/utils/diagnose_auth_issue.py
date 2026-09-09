#!/usr/bin/env python3
"""
Diagnose JWT Authentication Issue

This script checks the agent runtime and IdP configuration to identify why JWT
authentication is failing. It is IdP-aware: it reads the IDP_PROVIDER flag from
SSM and inspects only the SSM keys that belong to the active IdP —
`cognito-*` keys on the Cognito path, `okta-*` keys on the Okta path — so a
missing key for the *other* IdP is never reported as a failure.

Cognito path: compares the runtime authorizer's issuer + allowedClients against
the Cognito SSM contract, then mints a test token to confirm the claims line up.

Okta path: compares the runtime authorizer's discoveryUrl + allowedAudience
against the Okta SSM contract. No token is minted — Okta Identity Engine
tenants block the Resource Owner Password Credentials (ROPC) grant. To check a
token, sign in through the Streamlit UI (notebook 08, Authorization Code +
PKCE) and run:
    python deployment/5a-gateway-setup/decode_user_token.py --token <jwt>

Usage:
    python diagnose_auth_issue.py
"""

import argparse
import base64
import json
import os
import sys

import boto3

# Make the repo root importable (idp_config is a sibling module in utils/, but
# the package-qualified import keeps this consistent with every other module).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.idp_config import get_idp_provider

# SSM keys read per IdP. Nothing outside the active IdP's list is ever fetched,
# so a `cognito-*` key is not consulted (nor reported missing) when the flag is
# "okta", and vice versa.
COGNITO_PARAMS = [
    "/app/lakehouse-agent/agent-runtime-arn",
    "/app/lakehouse-agent/cognito-user-pool-id",
    "/app/lakehouse-agent/cognito-app-client-id",
    "/app/lakehouse-agent/cognito-region",
]

OKTA_PARAMS = [
    "/app/lakehouse-agent/agent-runtime-arn",
    "/app/lakehouse-agent/okta-org-url",
    "/app/lakehouse-agent/okta-auth-server-id",
    "/app/lakehouse-agent/okta-discovery-url",
    "/app/lakehouse-agent/okta-resource-server-audience",
]


def decode_jwt_payload(token):
    """Decode JWT token payload without verification"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding

        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def get_jwt_authorizer(region, runtime_arn):
    """
    Fetch the agent runtime's customJWTAuthorizer block.

    Returns:
        The customJWTAuthorizer dict, or None if the runtime has no JWT
        authorizer (guidance is printed in that case).
    """
    print("\n🔍 Checking Agent Runtime Configuration...")
    try:
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        response = client.get_agent_runtime(agentRuntimeArn=runtime_arn)
        runtime_config = response["agentRuntime"]
    except Exception as e:
        print(f"   ❌ Error getting agent runtime: {e}")
        import traceback

        traceback.print_exc()
        return None

    if "authorizerConfiguration" not in runtime_config:
        print("   ❌ No authorizer configuration found!")
        print("   ℹ️  Agent is using IAM SigV4 authentication")
        print("\n💡 Solution:")
        print("   Run: python lakehouse-agent/update_agent_authorizer.py")
        return None

    auth_config = runtime_config["authorizerConfiguration"]

    if "customJWTAuthorizer" not in auth_config:
        print("   ❌ No JWT authorizer configured!")
        print("\n💡 Solution:")
        print("   Run: python lakehouse-agent/update_agent_authorizer.py")
        return None

    return auth_config["customJWTAuthorizer"]


def diagnose_cognito(ssm, params, jwt_config):
    """Cognito-path diagnostics: issuer + allowedClients, then a live token mint."""
    discovery_url = jwt_config.get("discoveryUrl", "")
    allowed_clients = jwt_config.get("allowedClients", [])

    print("   ✅ JWT Authorizer configured")
    print(f"   Discovery URL: {discovery_url}")
    print(f"   Allowed Clients: {allowed_clients}")

    # Extract issuer from discovery URL
    configured_issuer = discovery_url.replace("/.well-known/openid-configuration", "")

    # Build expected issuer from Cognito config
    cognito_region = params["/app/lakehouse-agent/cognito-region"]
    cognito_pool_id = params["/app/lakehouse-agent/cognito-user-pool-id"]
    cognito_client_id = params["/app/lakehouse-agent/cognito-app-client-id"]

    expected_issuer = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_pool_id}"

    print("\n🔍 Comparing Issuers...")
    print(f"   Configured issuer: {configured_issuer}")
    print(f"   Expected issuer:   {expected_issuer}")

    if configured_issuer != expected_issuer:
        print("   ❌ MISMATCH!")
        print("\n💡 Solution:")
        print("   Run: python lakehouse-agent/update_agent_authorizer.py")
        return

    print("   ✅ Issuers match!")

    # Check client ID
    print("\n🔍 Comparing Client IDs...")
    print(f"   Configured clients: {allowed_clients}")
    print(f"   Expected client:    {cognito_client_id}")

    if cognito_client_id not in allowed_clients:
        print("   ❌ Client ID not in allowed list!")
        print("\n💡 Solution:")
        print("   Run: python lakehouse-agent/update_agent_authorizer.py")
        return

    print("   ✅ Client ID matches!")

    # Test authentication
    print("\n🔍 Testing Authentication...")
    try:
        cognito = boto3.client("cognito-idp", region_name=cognito_region)

        username = "policyholder001@example.com"
        password = "TempPass123!"

        # Get client secret
        client_secret = ssm.get_parameter(
            Name="/app/lakehouse-agent/cognito-app-client-secret",
            WithDecryption=True,
        )["Parameter"]["Value"]

        # Calculate SECRET_HASH
        import hashlib
        import hmac

        message = bytes(username + cognito_client_id, "utf-8")
        secret = bytes(client_secret, "utf-8")
        secret_hash = base64.b64encode(hmac.new(secret, message, digestmod=hashlib.sha256).digest()).decode()

        response = cognito.admin_initiate_auth(
            UserPoolId=cognito_pool_id,
            ClientId=cognito_client_id,
            AuthFlow="ADMIN_NO_SRP_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
                "SECRET_HASH": secret_hash,
            },
        )

        if "AuthenticationResult" in response:
            access_token = response["AuthenticationResult"]["AccessToken"]
            print(f"   ✅ Successfully authenticated as {username}")

            # Decode and check token
            claims = decode_jwt_payload(access_token)
            if claims:
                token_issuer = claims.get("iss")
                token_client_id = claims.get("client_id")

                print("\n📄 Token Claims:")
                print(f"   Issuer (iss): {token_issuer}")
                print(f"   Client ID: {token_client_id}")
                print(f"   Username: {claims.get('username')}")
                print(f"   Expires: {claims.get('exp')}")

                if token_issuer == configured_issuer and token_client_id in allowed_clients:
                    print("\n✅ ALL CHECKS PASSED!")
                    print("\n   Your configuration is correct.")
                    print("   If you're still getting errors, check:")
                    print("   1. Token hasn't expired")
                    print("   2. Network connectivity to AWS")
                    print("   3. Agent runtime is in ACTIVE state")
                else:
                    print("\n❌ Token claims don't match configuration!")
                    if token_issuer != configured_issuer:
                        print("   Issuer mismatch!")
                    if token_client_id not in allowed_clients:
                        print("   Client ID not allowed!")
        else:
            print("   ❌ Authentication failed")

    except Exception as e:
        print(f"   ❌ Error testing authentication: {e}")


def diagnose_okta(params, jwt_config):
    """
    Okta-path diagnostics: discoveryUrl + allowedAudience vs the Okta SSM contract.

    Configuration-side only. Okta Identity Engine tenants block the ROPC grant,
    so no token is minted here; token-side checks live in decode_user_token.py.
    """
    runtime_discovery_url = jwt_config.get("discoveryUrl", "")
    # The Okta runtime authorizer uses `allowedAudience` rather than
    # `allowedClients` — Okta access tokens carry the resource-server audience.
    runtime_allowed_audience = jwt_config.get("allowedAudience", [])

    print("   ✅ JWT Authorizer configured")
    print(f"   Discovery URL: {runtime_discovery_url}")
    print(f"   Allowed Audience: {runtime_allowed_audience}")

    expected_discovery_url = params["/app/lakehouse-agent/okta-discovery-url"]
    expected_audience = params["/app/lakehouse-agent/okta-resource-server-audience"]
    org_url = params["/app/lakehouse-agent/okta-org-url"]
    auth_server_id = params["/app/lakehouse-agent/okta-auth-server-id"]

    print("\n🔍 Comparing Discovery URLs...")
    print(f"   Configured discovery URL: {runtime_discovery_url}")
    print(f"   Expected discovery URL:   {expected_discovery_url}")
    discovery_match = runtime_discovery_url == expected_discovery_url
    if discovery_match:
        print("   ✅ Discovery URLs match!")
    else:
        print("   ❌ MISMATCH!")

    print("\n🔍 Comparing Audiences...")
    print(f"   Configured audience: {runtime_allowed_audience}")
    print(f"   Expected audience:   {expected_audience}")
    audience_match = expected_audience in runtime_allowed_audience
    if audience_match:
        print("   ✅ Audience matches!")
    else:
        print("   ❌ Expected audience not in runtime allowedAudience!")

    if not (discovery_match and audience_match):
        print("\n💡 Solution:")
        print("   Re-run: python deployment/6-lakehouse-agent/deploy_lakehouse_agent.py")
        print("   (recreates the runtime authorizer from the current Okta SSM contract)")
        return

    print("\n✅ ALL CONFIGURATION CHECKS PASSED!")
    print(f"\n   Expected token issuer: https://{org_url}/oauth2/{auth_server_id}")
    print("\n   Token minting is not attempted here: Okta Identity Engine tenants block")
    print("   the ROPC grant. To check a real token's claims against the Gateway:")
    print("   1. Sign in through the Streamlit UI (notebook 08, Authorization Code + PKCE)")
    print("   2. python deployment/5a-gateway-setup/decode_user_token.py --token <jwt>")


def main():
    # No options: the IdP is read from the IDP_PROVIDER flag in SSM, not a flag
    # here. argparse is present so `--help` documents the behavior and stray
    # arguments are rejected instead of silently ignored.
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose the agent runtime's JWT authorizer against the active IdP's SSM contract. "
            "Reads IDP_PROVIDER from SSM and inspects cognito-* keys on the Cognito path, "
            "okta-* keys on the Okta path."
        ),
        epilog="Token-side checks live elsewhere: python deployment/5a-gateway-setup/decode_user_token.py --token <jwt>",
    )
    parser.parse_args()

    print("=" * 80)
    print("JWT Authentication Diagnostics")
    print("=" * 80)

    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)

    print(f"\n📍 Region: {region}")

    idp_provider = get_idp_provider(ssm)
    print(f"📍 IDP_PROVIDER: {idp_provider}")

    # Check SSM parameters — only the active IdP's keys.
    print("\n🔍 Checking SSM Parameter Store...")

    params_to_check = COGNITO_PARAMS if idp_provider == "cognito" else OKTA_PARAMS

    params = {}
    missing_params = []

    for param_name in params_to_check:
        try:
            value = ssm.get_parameter(Name=param_name)["Parameter"]["Value"]
            params[param_name] = value
            print(f"   ✅ {param_name}: {value}")
        except ssm.exceptions.ParameterNotFound:
            missing_params.append(param_name)
            print(f"   ❌ {param_name}: NOT FOUND")

    if missing_params:
        print("\n❌ Missing SSM parameters!")
        print("\n💡 Solution:")
        print("   Run the setup scripts in order:")
        if idp_provider == "cognito":
            print("   1. python gateway-setup/setup_cognito.py")
            print("   2. python lakehouse-agent/deploy_lakehouse_agent.py")
        else:
            print("   1. python deployment/1-okta-setup/setup_okta.py  (writes okta-* keys)")
            print("   2. python deployment/6-lakehouse-agent/deploy_lakehouse_agent.py")
        return

    # Get agent runtime configuration
    jwt_config = get_jwt_authorizer(region, params["/app/lakehouse-agent/agent-runtime-arn"])
    if jwt_config is None:
        return

    if idp_provider == "cognito":
        diagnose_cognito(ssm, params, jwt_config)
    else:
        diagnose_okta(params, jwt_config)


if __name__ == "__main__":
    main()
