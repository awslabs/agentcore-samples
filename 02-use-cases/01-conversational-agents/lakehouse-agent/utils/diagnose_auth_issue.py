#!/usr/bin/env python3
"""
Diagnose JWT Authentication Issue

This script checks the agent runtime and Cognito configuration to identify
why JWT authentication is failing.
"""

import boto3
import json
import base64


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


def main():
    print("=" * 80)
    print("JWT Authentication Diagnostics")
    print("=" * 80)

    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)

    print(f"\n📍 Region: {region}")

    # Check SSM parameters
    print("\n🔍 Checking SSM Parameter Store...")

    params_to_check = [
        "/app/lakehouse-agent/agent-runtime-arn",
        "/app/lakehouse-agent/cognito-user-pool-id",
        "/app/lakehouse-agent/cognito-app-client-id",
        "/app/lakehouse-agent/cognito-region",
    ]

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
        print("   1. python gateway-setup/setup_cognito.py")
        print("   2. python lakehouse-agent/deploy_lakehouse_agent.py")
        return

    # Get agent runtime configuration
    print("\n🔍 Checking Agent Runtime Configuration...")
    try:
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        runtime_arn = params["/app/lakehouse-agent/agent-runtime-arn"]

        response = client.get_agent_runtime(agentRuntimeArn=runtime_arn)
        runtime_config = response["agentRuntime"]

        if "authorizerConfiguration" not in runtime_config:
            print("   ❌ No authorizer configuration found!")
            print("   ℹ️  Agent is using IAM SigV4 authentication")
            print("\n💡 Solution:")
            print("   Run: python lakehouse-agent/update_agent_authorizer.py")
            return

        auth_config = runtime_config["authorizerConfiguration"]

        if "customJWTAuthorizer" not in auth_config:
            print("   ❌ No JWT authorizer configured!")
            print("\n💡 Solution:")
            print("   Run: python lakehouse-agent/update_agent_authorizer.py")
            return

        jwt_config = auth_config["customJWTAuthorizer"]
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
            import hmac
            import hashlib

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

    except Exception as e:
        print(f"   ❌ Error getting agent runtime: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
