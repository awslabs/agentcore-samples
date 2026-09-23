#!/usr/bin/env python3
"""Test script for MCP gateway connectivity."""

import asyncio
import requests
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from langchain_mcp_adapters.tools import load_mcp_tools
from cx_agent_backend.infrastructure.aws.parameter_store_reader import (
    AWSParameterStoreReader,
)
from cx_agent_backend.infrastructure.aws.secret_reader import AWSSecretsReader
from langgraph.prebuilt import create_react_agent
from langchain_aws import ChatBedrock


def fetch_access_token(client_id, client_secret, token_url):
    """Fetch access token using client credentials flow."""
    # Try without explicit scopes first
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    print(f"Requesting token from: {token_url}")
    print(f"With client_id: {client_id}")

    response = requests.post(
        token_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    print(f"Token response status: {response.status_code}")
    print(f"Token response: {response.text}")

    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get("access_token")

        # Decode and inspect the token
        if access_token:
            import base64

            try:
                # JWT tokens have 3 parts separated by dots
                parts = access_token.split(".")
                if len(parts) >= 2:
                    # Decode the payload (second part)
                    payload = parts[1]
                    # Add padding if needed
                    payload += "=" * (4 - len(payload) % 4)
                    decoded = base64.b64decode(payload)
                    payload_json = json.loads(decoded)
                    print(f"\nToken payload: {json.dumps(payload_json, indent=2)}")
                    print(f"\nToken audience (aud): {payload_json.get('aud')}")
                    print(f"Token client_id: {payload_json.get('client_id')}")
                    print(f"Token scope: {payload_json.get('scope')}")
            except Exception as e:
                print(f"Could not decode token: {e}")

        return access_token
    else:
        print("Token request failed. Trying with scopes...")
        # Try with scopes
        data["scope"] = "gateway-api/read gateway-api/write"
        response2 = requests.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        print(
            f"With scopes - Status: {response2.status_code}, Response: {response2.text}"
        )
        if response2.status_code == 200:
            return response2.json().get("access_token")

    return None


async def test_mcp_gateway():
    """Test MCP gateway connection and tool retrieval."""
    parameter_store_reader = AWSParameterStoreReader()
    secret_reader = AWSSecretsReader()

    try:
        # Get gateway URL from parameter store
        gateway_url = parameter_store_reader.get_parameter(
            "/amazon/gateway_url", decrypt=True
        )
        if not gateway_url:
            print("Error: Gateway URL not found in parameter store")
            return

        print(f"Gateway URL: {gateway_url}")

        # Get client credentials from parameter store
        CLIENT_ID = parameter_store_reader.get_parameter(
            "/cognito/client_id", decrypt=True
        )
        client_secret = secret_reader.read_secret("cognito_client_secret")
        TOKEN_URL = parameter_store_reader.get_parameter(
            "/cognito/oauth_token_url", decrypt=True
        )
        if not all([CLIENT_ID, client_secret, TOKEN_URL]):
            print("Error: Missing Cognito credentials in parameter store")
            print(f"CLIENT_ID: {'✓' if CLIENT_ID else '✗'}")
            print(f"client_secret: {'✓' if client_secret else '✗'}")
            print(f"TOKEN_URL: {'✓' if TOKEN_URL else '✗'}")
            return

        # Fetch access token
        access_token = fetch_access_token(CLIENT_ID, client_secret, TOKEN_URL)

        if not access_token:
            print("Error: Failed to get access token")
            return

        print(f"Using access token: {access_token[:50]}...")

        # Test the gateway URL directly first
        test_response = requests.get(
            gateway_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10
        )
        print(f"Direct gateway test - Status: {test_response.status_code}")
        print(f"Direct gateway test - Response: {test_response.text[:200]}...")

        if test_response.status_code == 401:
            print("\nDebugging 401 error:")
            print("1. Check if gateway authorizer is configured correctly")
            print("2. Verify token audience matches gateway configuration")
            print("3. Ensure resource server scopes are properly configured")
            return

        async with streamablehttp_client(
            gateway_url, headers={"Authorization": f"Bearer {access_token}"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                # Initialize the connection
                await session.initialize()
                print(f"Connected to MCP server at {gateway_url}")

                # Get tools
                tools = await load_mcp_tools(session)
                print(f"\nFound {len(tools)} available tools:")
                model = ChatBedrock(
                    model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                    region_name="us-west-2",
                )

                agent = create_react_agent(model, tools)
                response = await agent.ainvoke({"messages": "what's latest in AI"})
                print(response)

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_mcp_gateway())
