#!/usr/bin/env python3
"""
Check M2M Client Configuration

This script checks the M2M client configuration and identifies any issues.

Usage:
    python check_m2m_client.py
"""

import boto3
import sys


def main():
    session = boto3.Session()
    region = session.region_name
    cognito = boto3.client("cognito-idp", region_name=region)
    ssm = boto3.client("ssm", region_name=region)

    print("=" * 70)
    print("Check M2M Client Configuration")
    print("=" * 70)

    # Get user pool ID and M2M client ID
    try:
        user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]
        m2m_client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-m2m-client-id")["Parameter"]["Value"]

        print("\n✅ Configuration found:")
        print(f"   User Pool ID: {user_pool_id}")
        print(f"   M2M Client ID: {m2m_client_id}")
    except Exception as e:
        print(f"\n❌ Error: Could not get configuration: {e}")
        sys.exit(1)

    # Get M2M client details
    print("\n📋 M2M Client Configuration:")
    print("=" * 70)

    try:
        response = cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=m2m_client_id)
        client = response["UserPoolClient"]

        print(f"\nClient Name: {client.get('ClientName', 'N/A')}")
        print("\n🔑 Authentication Flows:")
        print(f"   ExplicitAuthFlows: {client.get('ExplicitAuthFlows', [])}")
        print(f"   AllowedOAuthFlows: {client.get('AllowedOAuthFlows', [])}")
        print(
            f"   AllowedOAuthFlowsUserPoolClient: {client.get('AllowedOAuthFlowsUserPoolClient', False)}"
        )  # codeql[py/clear-text-logging-sensitive-data]

        print("\n🔐 OAuth Configuration:")
        print(
            f"   AllowedOAuthScopes: {client.get('AllowedOAuthScopes', [])}"
        )  # codeql[py/clear-text-logging-sensitive-data]
        print(f"   SupportedIdentityProviders: {client.get('SupportedIdentityProviders', [])}")
        print(f"   CallbackURLs: {client.get('CallbackURLs', [])}")
        print(f"   LogoutURLs: {client.get('LogoutURLs', [])}")

        print("\n🔒 Security:")
        print(f"   PreventUserExistenceErrors: {client.get('PreventUserExistenceErrors', 'N/A')}")

        # Check for issues
        print("\n🔍 Validation:")
        issues = []

        if not client.get("AllowedOAuthFlowsUserPoolClient"):
            issues.append("❌ AllowedOAuthFlowsUserPoolClient is False (must be True)")
        else:
            print("   ✅ AllowedOAuthFlowsUserPoolClient is True")

        if "client_credentials" not in client.get("AllowedOAuthFlows", []):
            issues.append("❌ client_credentials not in AllowedOAuthFlows")
        else:
            print("   ✅ client_credentials flow is enabled")

        if not client.get("AllowedOAuthScopes"):
            issues.append("❌ No OAuth scopes configured")
        else:
            print(f"   ✅ OAuth scopes configured: {len(client.get('AllowedOAuthScopes', []))} scopes")

        if client.get("ExplicitAuthFlows"):
            issues.append(
                f"⚠️  ExplicitAuthFlows should be empty for M2M-only client: {client.get('ExplicitAuthFlows')}"
            )
        else:
            print("   ✅ ExplicitAuthFlows is empty (M2M only)")

        # Check for callback URLs (some Cognito configs require this)
        if not client.get("CallbackURLs"):
            issues.append("⚠️  No CallbackURLs configured (may cause invalid_grant error)")
            print("   ⚠️  CallbackURLs is empty (may need dummy URL)")
        else:
            print("   ✅ CallbackURLs configured")

        if issues:
            print("\n❌ Issues Found:")
            for issue in issues:
                print(f"   {issue}")

            print("\n🔧 Recommended Fix:")
            print("   Run: python copy_m2m_client.py")
            print("   This will reconfigure the M2M client with correct settings")
        else:
            print("\n✅ M2M client configuration looks good!")

        # Test token request
        print("\n🧪 Testing Token Request:")
        print("=" * 70)

        try:
            domain = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-domain")["Parameter"]["Value"]
            client_secret = ssm.get_parameter(
                Name="/app/lakehouse-agent/cognito-m2m-client-secret",
                WithDecryption=True,
            )["Parameter"]["Value"]

            import requests
            import base64

            token_endpoint = f"{domain}/oauth2/token"
            credentials = f"{m2m_client_id}:{client_secret}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()

            print(f"   Token Endpoint: {token_endpoint}")
            print(f"   Client ID: {m2m_client_id}")
            print("   Attempting token request...")

            response = requests.post(
                token_endpoint,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {encoded_credentials}",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": " ".join(client.get("AllowedOAuthScopes", [])),
                },
            )

            if response.status_code == 200:
                print("\n   ✅ Token request successful!")
                token_data = response.json()
                print(f"   Access Token: {token_data['access_token'][:50]}...")
                print(f"   Token Type: {token_data['token_type']}")
                print(f"   Expires In: {token_data['expires_in']} seconds")
            else:
                print("\n   ❌ Token request failed!")
                print(f"   Status Code: {response.status_code}")
                print(f"   Response: {response.text}")

                if response.status_code == 400 and "invalid_grant" in response.text:
                    print("\n   💡 Possible causes of invalid_grant:")
                    print("      1. AllowedOAuthFlowsUserPoolClient not set to True")
                    print("      2. Missing CallbackURLs (add dummy URL)")
                    print("      3. Client not properly configured for client_credentials")
                    print("\n   🔧 Fix: python copy_m2m_client.py")

        except Exception as e:
            print(f"\n   ❌ Error testing token request: {e}")

    except Exception as e:
        print(f"\n❌ Error: Could not get client details: {e}")
        sys.exit(1)

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
