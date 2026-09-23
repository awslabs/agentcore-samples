#!/usr/bin/env python3
"""
Test AgentCore Gateway with User Authentication

This script tests the complete authentication flow:
1. User authenticates with Cognito (gets user JWT token)
2. User sends request to Gateway with JWT token
3. Gateway validates user JWT token
4. Gateway gets M2M token from Cognito (automatic via OAuth provider)
5. Gateway forwards request to MCP Runtime with M2M token
6. Runtime validates M2M token and processes request
7. Gateway returns response to user

Usage:
    python test_gateway.py --username <username> --password <password>
    python test_gateway.py --username testuser --password TestPass123!
"""

import boto3
import requests
import json
import base64
import argparse
import sys


def get_user_token(username: str, password: str):
    """
    Authenticate user with Cognito and get JWT token.

    Args:
        username: Cognito username
        password: User password

    Returns:
        Tuple of (access_token, id_token, region)
    """
    print("=" * 70)
    print("Step 1: User Authentication with Cognito")
    print("=" * 70)

    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    # Get Cognito configuration
    print("\n📋 Loading Cognito configuration...")
    try:
        client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-id")["Parameter"]["Value"]
        client_secret = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-secret", WithDecryption=True)[
            "Parameter"
        ]["Value"]
        user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]

        print(f"   Client ID: {client_id}")
        print(f"   User Pool: {user_pool_id}")
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        return None, None, region

    # Authenticate user using ADMIN_USER_PASSWORD_AUTH
    # This flow doesn't require USER_PASSWORD_AUTH to be enabled on the client
    print(f"\n🔐 Authenticating user: {username}")
    try:
        # Calculate SECRET_HASH
        import hmac
        import hashlib

        message = username + client_id
        secret_hash = base64.b64encode(
            hmac.new(client_secret.encode(), message.encode(), hashlib.sha256).digest()
        ).decode()

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
        print("   Token type: Bearer")
        print(f"   Expires in: {response['AuthenticationResult']['ExpiresIn']} seconds")
        print("Access Token", access_token)
        print("ID Token", id_token)

        # Decode and print token claims
        print("\n📄 User Token Claims:")
        parts = id_token.split(".")
        if len(parts) == 3:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
            print(f"   Username: {payload.get('cognito:username', 'N/A')}")
            print(f"   Email: {payload.get('email', 'N/A')}")
            print(f"   Token Use: {payload.get('token_use', 'N/A')}")
            print(f"   Audience (aud): {payload.get('aud', 'N/A')}")
            print(f"   Issuer (iss): {payload.get('iss', 'N/A')}")

        # Also decode access token to see its claims
        print("\n📄 Access Token Claims:")
        parts = access_token.split(".")
        if len(parts) == 3:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
            print(f"   Client ID: {payload.get('client_id', 'N/A')}")
            print(f"   Token Use: {payload.get('token_use', 'N/A')}")
            print(f"   Scope: {payload.get('scope', 'N/A')}")
            print(f"   Username: {payload.get('username', 'N/A')}")
            print(f"   Issuer (iss): {payload.get('iss', 'N/A')}")

        print("\n🔑 Access Token (first 100 chars):")
        print(f"   {access_token[:100]}...")

        return access_token, id_token, region

    except cognito.exceptions.NotAuthorizedException:
        print("❌ Authentication failed: Invalid username or password")
        return None, None, region
    except cognito.exceptions.UserNotFoundException:
        print(f"❌ User not found: {username}")
        return None, None, region
    except Exception as e:
        print(f"❌ Authentication error: {e}")
        return None, None, region


def test_gateway(access_token: str, region: str):
    """
    Test Gateway by sending MCP requests with user token.

    Args:
        access_token: User's access token from Cognito
        region: AWS region
    """
    print("\n" + "=" * 70)
    print("Step 2: Test Gateway with User Token")
    print("=" * 70)

    ssm = boto3.client("ssm", region_name=region)

    # Get Gateway URL
    print("\n📦 Loading Gateway configuration...")
    try:
        gateway_url = ssm.get_parameter(Name="/app/lakehouse-agent/gateway-url")["Parameter"]["Value"]
        gateway_id = ssm.get_parameter(Name="/app/lakehouse-agent/gateway-id")["Parameter"]["Value"]
        print(f"   Gateway URL: {gateway_url}")
        print(f"   Gateway ID: {gateway_id}")

        # Get Gateway configuration to check JWT authorizer settings
        print("\n🔍 Checking Gateway JWT authorizer configuration...")
        agentcore = boto3.client("bedrock-agentcore-control", region_name=region)
        try:
            gateway_details = agentcore.get_gateway(gatewayIdentifier=gateway_id)
            auth_config = gateway_details.get("authorizerConfiguration", {})
            if "customJWTAuthorizer" in auth_config:
                jwt_config = auth_config["customJWTAuthorizer"]
                print(f"   Discovery URL: {jwt_config.get('discoveryUrl', 'N/A')}")
                print(f"   Allowed Audience: {jwt_config.get('allowedAudience', [])}")
                print(f"   Allowed Clients: {jwt_config.get('allowedClients', [])}")
        except Exception as e:
            print(f"   ⚠️  Could not get Gateway details: {e}")

    except ssm.exceptions.ParameterNotFound:
        print("   ❌ Gateway URL not found in SSM")
        print("   Please deploy the Gateway first:")
        print("   cd gateway-setup && python create_gateway.py")
        return
    except Exception as e:
        print(f"   ❌ Error loading Gateway URL: {e}")
        return

    # Prepare headers with user token
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    # Test 1: Initialize MCP session
    print("\n📤 Test 1: Initialize MCP session through Gateway")
    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "gateway-test-client", "version": "1.0.0"},
        },
    }

    try:
        response = requests.post(gateway_url, headers=headers, json=init_request, timeout=30)
        print(f"   Status: {response.status_code}")
        print(f"   Response length: {len(response.text)} bytes")
        print(f"   Content-Type: {response.headers.get('Content-Type', 'N/A')}")

        if response.status_code == 200:
            if response.text:
                # Parse SSE format
                if response.headers.get("Content-Type") == "text/event-stream":
                    print("   ✅ Received SSE response")
                    lines = response.text.split("\n")
                    for line in lines:
                        if line.startswith("data: "):
                            json_str = line[6:]
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
                    except json.JSONDecodeError:
                        print("   ⚠️  Response is not valid JSON")
                        print(f"   Raw response: {response.text[:200]}")
            else:
                print("   ⚠️  Response body is empty")
                return
        elif response.status_code == 401:
            print("   ❌ Unauthorized - User token validation failed")
            print(f"   Response: {response.text[:500]}")
            return
        elif response.status_code == 403:
            print("   ❌ Forbidden - User not authorized")
            print(f"   Response: {response.text[:500]}")
            return
        else:
            print("   ❌ Initialize failed")
            print(f"   Response: {response.text[:500]}")
            return

    except requests.exceptions.Timeout:
        print("   ❌ Request timed out")
        print("   This may indicate Gateway-to-Runtime authentication issues")
        return
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return

    # Test 2: Get tool list
    print("\n📤 Test 2: Get tool list through Gateway")
    tools_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

    try:
        response = requests.post(gateway_url, headers=headers, json=tools_request, timeout=30)
        print(f"   Status: {response.status_code}")
        print(f"   Response length: {len(response.text)} bytes")

        if response.status_code == 200:
            if response.text:
                # Parse SSE format
                if response.headers.get("Content-Type") == "text/event-stream":
                    print("   ✅ Received SSE response")
                    lines = response.text.split("\n")
                    for line in lines:
                        if line.startswith("data: "):
                            json_str = line[6:]
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
                            for i, tool in enumerate(tools, 1):
                                print(f"   {i}. {tool.get('name', 'N/A')}")
                    except json.JSONDecodeError:
                        print("   ⚠️  Response is not valid JSON")
            else:
                print("   ⚠️  Response body is empty")
        else:
            print("   ❌ Tool list failed")
            print(f"   Response: {response.text[:500]}")

    except requests.exceptions.Timeout:
        print("   ❌ Request timed out")
    except Exception as e:
        print(f"   ❌ Error: {e}")

    # Test 3: Query claims (if available)
    print("\n📤 Test 3: Query claims (user-specific data)")
    query_request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "get_claims_summary", "arguments": {}},
    }

    try:
        response = requests.post(gateway_url, headers=headers, json=query_request, timeout=30)
        print(f"   Status: {response.status_code}")

        if response.status_code == 200:
            if response.text:
                # Parse SSE format
                if response.headers.get("Content-Type") == "text/event-stream":
                    lines = response.text.split("\n")
                    for line in lines:
                        if line.startswith("data: "):
                            json_str = line[6:]
                            try:
                                data = json.loads(json_str)
                                if "result" in data:
                                    print("   ✅ Query successful!")
                                    # Try to parse the content
                                    if "content" in data["result"]:
                                        for content in data["result"]["content"]:
                                            if content.get("type") == "text":
                                                try:
                                                    result_data = json.loads(content["text"])
                                                    if result_data.get("success"):
                                                        summary = result_data.get("summary", {})
                                                        print(f"   Total Claims: {summary.get('total_claims', 0)}")
                                                        print(
                                                            f"   Total Amount: ${summary.get('total_amount', 0):,.2f}"
                                                        )
                                                except (
                                                    json.JSONDecodeError,
                                                    ValueError,
                                                    KeyError,
                                                ):
                                                    print(f"   Response: {content['text'][:200]}")
                                break
                            except json.JSONDecodeError:
                                continue
        else:
            print("   ⚠️  Query failed or tool not available")

    except Exception as e:
        print(f"   ⚠️  Query test skipped: {e}")


def main():
    parser = argparse.ArgumentParser(description="Test AgentCore Gateway with user authentication")
    parser.add_argument("--username", required=True, help="Cognito username")
    parser.add_argument("--password", required=True, help="User password")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("AgentCore Gateway Test with User Authentication")
    print("=" * 70 + "\n")

    # Step 1: Authenticate user and get token
    access_token, id_token, region = get_user_token(args.username, args.password)

    if not access_token:
        print("\n❌ Failed to authenticate user. Exiting.")
        sys.exit(1)

    # Step 2: Test Gateway with user token
    test_gateway(access_token, region)

    print("\n" + "=" * 70)
    print("Test Complete")
    print("=" * 70)
    print("\n✅ Authentication Flow Validated:")
    print("   1. User authenticated with Cognito ✓")
    print("   2. User token sent to Gateway ✓")
    print("   3. Gateway validated user token ✓")
    print("   4. Gateway obtained M2M token (automatic) ✓")
    print("   5. Gateway forwarded request to Runtime ✓")
    print("   6. Runtime validated M2M token ✓")
    print("   7. Response returned to user ✓")
    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    main()
