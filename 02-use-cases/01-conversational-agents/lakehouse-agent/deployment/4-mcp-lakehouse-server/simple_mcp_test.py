#!/usr/bin/env python3
"""
Simple MCP Server Test

This script:
1. Gets M2M token from Cognito
2. Prints token for validation
3. Uses token to invoke MCP server and get tool list
"""

import boto3
import requests
import json
import base64


def get_m2m_token():
    """Get M2M access token from Cognito."""
    print("=" * 70)
    print("Step 1: Get M2M Token from Cognito")
    print("=" * 70)

    try:
        session = boto3.Session()
        region = session.region_name
        ssm = boto3.client("ssm", region_name=region)
        cognito = boto3.client("cognito-idp", region_name=region)

        # Get M2M client credentials from SSM
        print("\n📋 Loading M2M client credentials from SSM...")
        client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-m2m-client-id")["Parameter"]["Value"]
        client_secret = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-m2m-client-secret", WithDecryption=True)[
            "Parameter"
        ]["Value"]
        domain = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-domain")["Parameter"]["Value"]
        user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        print("   Please check your AWS credentials and SSM parameters")
        return None, None

    print(f"   Client ID: {client_id}")
    print(f"   Domain: {domain}")
    print(f"   User Pool: {user_pool_id}")

    # Get configured OAuth scopes
    print("\n🔍 Getting OAuth scopes from client configuration...")
    client_details = cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
    allowed_scopes = client_details["UserPoolClient"].get("AllowedOAuthScopes", [])
    scope_string = " ".join(allowed_scopes)
    print(f"   Scopes: {scope_string}")  # codeql[py/clear-text-logging-sensitive-data]

    # Request token
    print("\n🔐 Requesting access token...")
    token_endpoint = f"{domain}/oauth2/token"
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    response = requests.post(
        token_endpoint,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}",
        },
        data={"grant_type": "client_credentials", "scope": scope_string},
    )

    if response.status_code != 200:
        print(f"❌ Token request failed: {response.status_code}")
        print(f"   Response: {response.text}")
        return None, region

    token_data = response.json()
    access_token = token_data["access_token"]

    print("✅ Token obtained successfully!")
    print(f"   Token type: {token_data.get('token_type', 'N/A')}")
    print(f"   Expires in: {token_data.get('expires_in', 'N/A')} seconds")

    # Decode and print token claims
    print("\n📄 Token Claims:")
    parts = access_token.split(".")
    if len(parts) == 3:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        print(f"   Issuer: {payload.get('iss', 'N/A')}")
        print(f"   Client ID: {payload.get('client_id', 'N/A')}")
        print(f"   Scope: {payload.get('scope', 'N/A')}")
        print(f"   Token Use: {payload.get('token_use', 'N/A')}")

    print("\n🔑 Access Token (first 100 chars):")
    print(f"   {access_token[:100]}...")

    return access_token, region


def test_mcp_server(access_token, region):
    """Test MCP server by getting tool list."""
    print("\n" + "=" * 70)
    print("Step 2: Invoke MCP Server to Get Tool List")
    print("=" * 70)

    ssm = boto3.client("ssm", region_name=region)

    # Get runtime ARN
    print("\n📦 Loading runtime configuration...")
    try:
        runtime_arn = ssm.get_parameter(Name="/app/lakehouse-agent/mcp-server-runtime-arn")["Parameter"]["Value"]
        print(f"   Runtime ARN: {runtime_arn}")
    except ssm.exceptions.ParameterNotFound:
        print(f"   ❌ Runtime ARN not found in region {region}")
        print("   Please deploy the MCP server first or check your region")
        return
    except Exception as e:
        print(f"   ❌ Error loading runtime ARN: {e}")
        return

    # Build MCP endpoint URL
    encoded_arn = runtime_arn.replace(":", "%3A").replace("/", "%2F")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
    print(f"   Endpoint: {url}")

    # Prepare headers
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    # Test 1: Initialize MCP session
    print("\n📤 Test 1: Initialize MCP session")
    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "simple-test-client", "version": "1.0.0"},
        },
    }

    try:
        response = requests.post(url, headers=headers, json=init_request, timeout=10)
        print(f"   Status: {response.status_code}")
        print(f"   Response length: {len(response.text)} bytes")
        print(f"   Content-Type: {response.headers.get('Content-Type', 'N/A')}")

        if response.status_code == 200:
            if response.text:
                # Parse SSE format
                if response.headers.get("Content-Type") == "text/event-stream":
                    print("   ✅ Received SSE response")
                    # Extract JSON from SSE format
                    lines = response.text.split("\n")
                    for line in lines:
                        if line.startswith("data: "):
                            json_str = line[6:]  # Remove 'data: ' prefix
                            try:
                                data = json.loads(json_str)
                                print("   ✅ Initialize successful!")
                                if "result" in data:
                                    server_info = data["result"].get("serverInfo", {})
                                    print(f"   Server Name: {server_info.get('name', 'N/A')}")
                                    print(f"   Server Version: {server_info.get('version', 'N/A')}")
                                    print(f"   Protocol Version: {data['result'].get('protocolVersion', 'N/A')}")
                                break
                            except json.JSONDecodeError:
                                continue
                else:
                    try:
                        data = response.json()
                        print("   ✅ Initialize successful!")
                        if "result" in data:
                            server_info = data["result"].get("serverInfo", {})
                            print(f"   Server Name: {server_info.get('name', 'N/A')}")
                            print(f"   Server Version: {server_info.get('version', 'N/A')}")
                            print(f"   Protocol Version: {data['result'].get('protocolVersion', 'N/A')}")
                    except json.JSONDecodeError as je:
                        print(f"   ⚠️  Response is not valid JSON: {je}")
                        print(f"   Raw response (first 500 chars): {response.text[:500]}")
            else:
                print("   ⚠️  Response body is empty")
                return
        else:
            print("   ❌ Initialize failed")
            if response.text:
                print(f"   Response: {response.text[:500]}")
            else:
                print("   Response body is empty")
            return

    except requests.exceptions.Timeout:
        print("   ❌ Request timed out")
        return
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return

    # Test 2: Get tool list
    print("\n📤 Test 2: Get tool list")
    tools_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

    try:
        response = requests.post(url, headers=headers, json=tools_request, timeout=10)
        print(f"   Status: {response.status_code}")
        print(f"   Response length: {len(response.text)} bytes")

        if response.status_code == 200:
            if response.text:
                # Parse SSE format
                if response.headers.get("Content-Type") == "text/event-stream":
                    print("   ✅ Received SSE response")
                    # Extract JSON from SSE format
                    lines = response.text.split("\n")
                    for line in lines:
                        if line.startswith("data: "):
                            json_str = line[6:]  # Remove 'data: ' prefix
                            try:
                                data = json.loads(json_str)
                                print("   ✅ Tool list retrieved!")

                                if "result" in data and "tools" in data["result"]:
                                    tools = data["result"]["tools"]
                                    print(f"\n   📋 Available Tools ({len(tools)}):")
                                    print("   " + "=" * 66)
                                    for i, tool in enumerate(tools, 1):
                                        print(f"\n   {i}. {tool.get('name', 'N/A')}")
                                        print(f"      Description: {tool.get('description', 'N/A')}")
                                        if "inputSchema" in tool and "properties" in tool["inputSchema"]:
                                            props = tool["inputSchema"]["properties"]
                                            if props:
                                                print(f"      Parameters: {', '.join(props.keys())}")
                                else:
                                    print(f"   Response: {json.dumps(data, indent=2)}")
                                break
                            except json.JSONDecodeError:
                                continue
                else:
                    try:
                        data = response.json()
                        print("   ✅ Tool list retrieved!")

                        if "result" in data and "tools" in data["result"]:
                            tools = data["result"]["tools"]
                            print(f"\n   📋 Available Tools ({len(tools)}):")
                            print("   " + "=" * 66)
                            for i, tool in enumerate(tools, 1):
                                print(f"\n   {i}. {tool.get('name', 'N/A')}")
                                print(f"      Description: {tool.get('description', 'N/A')}")
                                if "inputSchema" in tool and "properties" in tool["inputSchema"]:
                                    props = tool["inputSchema"]["properties"]
                                    if props:
                                        print(f"      Parameters: {', '.join(props.keys())}")
                        else:
                            print(f"   Response: {json.dumps(data, indent=2)}")
                    except json.JSONDecodeError:
                        print("   ⚠️  Response is not valid JSON")
                        print(f"   Raw response: {response.text[:200]}")
            else:
                print("   ⚠️  Response body is empty")
        else:
            print("   ❌ Tool list failed")
            if response.text:
                print(f"   Response: {response.text[:500]}")
            else:
                print("   Response body is empty")

    except requests.exceptions.Timeout:
        print("   ❌ Request timed out")
    except Exception as e:
        print(f"   ❌ Error: {e}")


def main():
    print("\n" + "=" * 70)
    print("Simple MCP Server Test with M2M Authentication")
    print("=" * 70 + "\n")

    # Step 1: Get token
    access_token, region = get_m2m_token()

    if not access_token:
        print("\n❌ Failed to get access token. Exiting.")
        return

    # Step 2: Test MCP server
    test_mcp_server(access_token, region)

    print("\n" + "=" * 70)
    print("Test Complete")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
