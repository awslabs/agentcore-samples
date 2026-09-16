#!/usr/bin/env python3
"""
Check Okta M2M Client Configuration

This script verifies the Okta OIDC application's client-credentials
configuration and tests that an M2M token can be acquired against the
custom authorization server's lakehouse-api audience.

Note: Unlike the original Cognito demo (which had a separate M2M-only app
client), Okta uses a single OIDC application for both user authentication
and machine-to-machine flows. The same client_id + client_secret can be used
for both. This script verifies the M2M side works.

Usage:
    python check_m2m_client.py
"""

import sys

import boto3
import requests


def main():
    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)

    print("=" * 70)
    print("Check Okta M2M Client Configuration")
    print("=" * 70)

    # Load configuration from SSM.
    try:
        org_url = ssm.get_parameter(Name="/app/lakehouse-agent/okta-org-url")["Parameter"]["Value"]
        auth_server_id = ssm.get_parameter(Name="/app/lakehouse-agent/okta-auth-server-id")["Parameter"]["Value"]
        client_id = ssm.get_parameter(Name="/app/lakehouse-agent/okta-app-client-id")["Parameter"]["Value"]
        client_secret = ssm.get_parameter(
            Name="/app/lakehouse-agent/okta-app-client-secret",
            WithDecryption=True,
        )["Parameter"]["Value"]
        audience = ssm.get_parameter(Name="/app/lakehouse-agent/okta-resource-server-audience")["Parameter"]["Value"]

        print("\n✅ Configuration found:")
        print(f"   Org URL:        {org_url}")
        print(f"   Auth Server ID: {auth_server_id}")
        print(f"   Client ID:      {client_id}")
        print(f"   Audience:       {audience}")
    except Exception as e:
        print(f"\n❌ Error: Could not load configuration from SSM: {e}")
        print("   Run setup_okta.py first.")
        sys.exit(1)

    # Test client-credentials token request against the custom auth server.
    print("\n🧪 Testing M2M token request (client_credentials flow):")
    print("=" * 70)

    token_endpoint = f"https://{org_url}/oauth2/{auth_server_id}/v1/token"

    print(f"   Token Endpoint: {token_endpoint}")
    print("   Scope:          claims.query")
    print("   Attempting token request...")

    try:
        response = requests.post(
            token_endpoint,
            auth=(client_id, client_secret),
            data={
                "grant_type": "client_credentials",
                "scope": "claims.query",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        if response.status_code == 200:
            token_data = response.json()
            print("\n   ✅ Token request successful!")
            print(f"   Access Token: {token_data['access_token'][:50]}...")
            print(f"   Token Type:   {token_data['token_type']}")
            print(f"   Expires In:   {token_data['expires_in']} seconds")
            print(f"   Scope:        {token_data.get('scope', 'N/A')}")
        else:
            print("\n   ❌ Token request failed!")
            print(f"   Status Code: {response.status_code}")
            print(f"   Response:    {response.text}")

            if response.status_code == 400:
                print("\n   💡 Common causes:")
                print("      1. Client secret doesn't match — re-run setup_okta.py to refresh")
                print("      2. Scope `claims.query` not enabled on the auth server — check setup_okta.py output")
                print("      3. App not assigned the `claims.query` scope under app's API access policy")
            sys.exit(1)

    except Exception as e:
        print(f"\n   ❌ Error during token request: {e}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("✅ M2M client check passed.")


if __name__ == "__main__":
    main()
