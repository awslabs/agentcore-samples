#!/usr/bin/env python3
"""
Simple test of agent runtime with JWT authentication
"""

import json
import uuid
import requests
from config import config


def get_cognito_token():
    """Get Cognito bearer token using client_credentials flow"""
    print("🔑 Getting Cognito bearer token...")

    cognito_domain = config.COGNITO_DOMAIN
    client_id = config.COGNITO_APP_CLIENT_ID
    client_secret = config.COGNITO_APP_CLIENT_SECRET
    scope = config.COGNITO_SCOPE_QUERY

    # Fix scope format: replace slashes with dots
    scope = scope.replace("/claims/", "/claims.")

    token_url = f"{cognito_domain}/oauth2/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
    }

    try:
        response = requests.post(token_url, data=data, timeout=10)
        response.raise_for_status()
        token = response.json().get("access_token")
        print(f"✅ Token obtained: {token[:20]}...")
        return token
    except Exception as e:
        print(f"❌ Failed to get token: {e}")
        return None


def test_agent_simple():
    """Test agent with a simple prompt using JWT authentication"""
    print("=" * 60)
    print("🧪 Testing Agent Runtime (Simple with JWT)")
    print("=" * 60)

    runtime_arn = config.RUNTIME_ARN
    runtime_id = config.RUNTIME_ID
    region = config.AWS_REGION

    print(f"\nRuntime ARN: {runtime_arn}")
    print(f"Runtime ID: {runtime_id}")
    print(f"Region: {region}")

    # Get Cognito token
    bearer_token = get_cognito_token()
    if not bearer_token:
        print("\n❌ Cannot proceed without token")
        return False

    try:
        # Build runtime endpoint URL using the correct format
        # Format: https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations
        escaped_arn = requests.utils.quote(runtime_arn, safe="")
        base_url = f"https://bedrock-agentcore.{region}.amazonaws.com"
        runtime_url = f"{base_url}/runtimes/{escaped_arn}/invocations"

        # Payload with bearer token for Gateway calls
        payload = {
            "prompt": "Hello, can you introduce yourself?",
            "bearer_token": bearer_token,  # Pass token in payload for agent to use with Gateway
        }

        # Generate session ID
        session_id = f"test-session-{uuid.uuid4().hex}"

        print("\n📤 Sending request...")
        print(f"   URL: {runtime_url}")
        print(f"   Prompt: {payload['prompt']}")
        print(f"   Session ID: {session_id}")
        print(f"   Bearer token: {bearer_token[:20]}...")

        # Make direct HTTPS request with OAuth bearer token
        # The runtime is configured for JWT auth, so we must use Authorization header
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        }

        response = requests.post(
            runtime_url,
            headers=headers,
            params={"qualifier": "DEFAULT"},
            json=payload,
            timeout=60,  # Increased timeout for agent processing
        )

        response.raise_for_status()
        response_data = response.json()

        print("\n✅ Agent response:")
        print(json.dumps(response_data, indent=2))
        return True

    except Exception as e:
        print(f"\n❌ Error: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"   Status: {e.response.status_code}")
            print(f"   Response: {e.response.text}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_agent_simple()
    print("\n" + "=" * 60)
    if success:
        print("✅ Simple test passed!")
    else:
        print("❌ Simple test failed")
    print("=" * 60)
