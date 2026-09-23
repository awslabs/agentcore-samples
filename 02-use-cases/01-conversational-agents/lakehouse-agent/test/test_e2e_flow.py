#!/usr/bin/env python3
"""
Test end-to-end flow: Cognito Token → Runtime → Agent → Gateway → MCP Server
"""

import json
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

    print(f"   Domain: {cognito_domain}")
    print(f"   Client ID: {client_id}")
    print(f"   Scope: {scope}")

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
        # Print response details for debugging
        if hasattr(e, "response") and e.response is not None:
            print(f"   Response status: {e.response.status_code}")
            print(f"   Response body: {e.response.text}")
        return None


def invoke_agent_runtime(bearer_token: str, prompt: str = "Show me all my claims"):
    """Invoke the lakehouse agent runtime with bearer token via JWT authentication"""
    print("\n🤖 Invoking agent runtime...")
    print(f"   Prompt: {prompt}")

    region = config.AWS_REGION

    try:
        # Build runtime endpoint URL using the correct format
        # Format: https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations
        runtime_arn = config.RUNTIME_ARN
        escaped_arn = requests.utils.quote(runtime_arn, safe="")
        base_url = f"https://bedrock-agentcore.{region}.amazonaws.com"
        runtime_url = f"{base_url}/runtimes/{escaped_arn}/invocations"

        # Payload with bearer token for Gateway calls
        payload = {
            "prompt": prompt,
            "bearer_token": bearer_token,  # Pass token in payload for agent to use with Gateway
        }

        print(f"   Runtime URL: {runtime_url}")
        print(f"   Bearer token: {bearer_token[:20]}...")

        # Generate a session ID that meets the minimum length requirement (33 chars)
        import uuid

        session_id = f"test-session-{uuid.uuid4().hex}"

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
            timeout=30,
        )

        response.raise_for_status()
        response_data = response.json()

        print("\n✅ Agent response:")
        print(json.dumps(response_data, indent=2))
        return response_data

    except Exception as e:
        print(f"\n❌ Error invoking agent: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"   Status: {e.response.status_code}")
            print(f"   Response: {e.response.text}")
        import traceback

        traceback.print_exc()
        return None


def main():
    print("=" * 60)
    print("🧪 Testing End-to-End Flow")
    print("=" * 60)

    # Step 1: Get Cognito token
    token = get_cognito_token()
    if not token:
        print("\n❌ Cannot proceed without token")
        return

    # Step 2: Invoke agent runtime with token
    response = invoke_agent_runtime(token)

    if response:
        print("\n" + "=" * 60)
        print("✅ End-to-end test completed!")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("❌ End-to-end test failed")
        print("=" * 60)


if __name__ == "__main__":
    main()
