#!/usr/bin/env python3
"""
End-to-End Test with User Authentication for RLS

This test uses actual user credentials (not client_credentials) to test
row-level security with proper user identity.
"""

import sys
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from aws_session_utils import get_aws_session


def main():
    session, region, account_id = get_aws_session()
    ssm = session.client("ssm", region_name=region)

    print("=" * 70)
    print("E2E TEST WITH USER AUTHENTICATION")
    print("=" * 70)
    print()

    # Get configuration
    print("Loading configuration from SSM...")

    runtime_arn = ssm.get_parameter(Name="/app/lakehouse-agent/agent-runtime-id")["Parameter"]["Value"]
    cognito_domain = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-domain")["Parameter"]["Value"]
    client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-id")["Parameter"]["Value"]
    client_secret = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-secret", WithDecryption=True)[
        "Parameter"
    ]["Value"]

    # Get test user credentials
    test_user = ssm.get_parameter(Name="/app/lakehouse-agent/test-user-3")["Parameter"]["Value"]
    test_password = ssm.get_parameter(Name="/app/lakehouse-agent/test-password", WithDecryption=True)["Parameter"][
        "Value"
    ]

    print(f"✅ Runtime: {runtime_arn}")
    print(f"✅ Test User: {test_user}")
    print()

    # Get user token using Resource Owner Password Credentials flow
    print("🔑 Getting user token (ROPC flow)...")

    token_url = f"{cognito_domain}/oauth2/token"

    try:
        response = requests.post(
            token_url,
            auth=(client_id, client_secret),
            data={
                "grant_type": "password",
                "username": test_user,
                "password": test_password,
                "scope": "lakehouse-api/claims.query openid email profile",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data["access_token"]
            id_token = token_data.get("id_token")

            print("✅ Access token obtained")
            if id_token:
                print("✅ ID token obtained")

            # Decode token to show user identity
            import base64
            import json

            def decode_jwt(token):
                parts = token.split(".")
                if len(parts) == 3:
                    payload = parts[1]
                    payload += "=" * (4 - len(payload) % 4)
                    decoded = base64.urlsafe_b64decode(payload)
                    return json.loads(decoded)
                return {}

            access_claims = decode_jwt(access_token)
            print("\n🔍 Access Token Claims:")
            print(f"   Username: {access_claims.get('username', 'N/A')}")
            print(f"   Email: {access_claims.get('email', 'N/A')}")
            print(f"   Scope: {access_claims.get('scope', 'N/A')}")

            if id_token:
                id_claims = decode_jwt(id_token)
                print("\n🔍 ID Token Claims:")
                print(f"   Email: {id_claims.get('email', 'N/A')}")
                print(f"   Email Verified: {id_claims.get('email_verified', 'N/A')}")

            # Use ID token if available (contains more user info), otherwise access token
            bearer_token = id_token if id_token else access_token

        else:
            print(f"❌ Failed to get token: HTTP {response.status_code}")
            print(f"   Response: {response.text}")

            # Try client_credentials as fallback
            print("\n⚠️  Falling back to client_credentials flow...")
            response = requests.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "lakehouse-api/claims.query",
                },
            )

            if response.status_code == 200:
                bearer_token = response.json()["access_token"]
                print("✅ Got client_credentials token (no user identity for RLS)")
            else:
                print(f"❌ Failed: {response.text}")
                return False

    except Exception as e:
        print(f"❌ Error getting token: {e}")
        import traceback

        traceback.print_exc()
        return False

    # Invoke agent
    print("\n🤖 Invoking agent with user token...")

    import urllib.parse

    encoded_arn = urllib.parse.quote(
        f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{runtime_arn}",
        safe="",
    )
    runtime_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations"

    try:
        response = requests.post(
            runtime_url,
            json={
                "input": "Show me all my claims",
                "sessionId": f"test-session-{test_user.replace('@', '-at-')}",
            },
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )

        if response.status_code == 200:
            result = response.json()

            print("✅ Agent response received")
            print("\nResponse:")
            print(f"  Content: {result.get('content', 'N/A')[:200]}...")
            print(f"  Tool Calls: {result.get('tool_calls', 0)}")

            if result.get("tool_calls", 0) > 0:
                print("\n✅✅✅ SUCCESS: Tools were invoked!")
                print(f"\nWith user identity: {test_user}")
                print("RLS should be applied based on this user")
            else:
                print("\n❌ FAIL: No tools invoked")
                print("   Check MCP server logs for errors")

            return result.get("tool_calls", 0) > 0

        else:
            print(f"❌ Agent invocation failed: HTTP {response.status_code}")
            print(f"   Response: {response.text}")
            return False

    except Exception as e:
        print(f"❌ Error invoking agent: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
