#!/usr/bin/env python3
"""
Test script to verify AgentCore Gateway tool generation.

This script:
1. Loads gateway configuration
2. Lists all MCP tools generated from OpenAPI spec
3. Verifies expected tools exist
4. Tests tool invocation (optional)
"""

import boto3
import json
from pathlib import Path


def load_gateway_config():
    """Load gateway configuration from file"""
    config_path = Path(".agentcore-gateway-config.json")

    if not config_path.exists():
        print("❌ Gateway configuration not found")
        print("Run: ./deploy-agentcore-gateway.sh first")
        return None

    with open(config_path, "r") as f:
        return json.load(f)


def list_gateway_tools(agentcore_client, gateway_id):
    """List all tools available from the gateway"""
    print("Listing MCP tools from gateway...")
    print()

    try:
        # List gateway targets
        targets_response = agentcore_client.list_gateway_targets(gatewayIdentifier=gateway_id)

        if "items" not in targets_response or len(targets_response["items"]) == 0:
            print("❌ No targets found in gateway")
            return []

        print(f"✓ Found {len(targets_response['items'])} target(s)")
        print()

        # For each target, describe it to see the tools
        all_tools = []
        for target in targets_response["items"]:
            target_id = target["targetId"]
            target_name = target["name"]

            print(f"Target: {target_name}")
            print(f"  ID: {target_id}")

            # Get target details
            target_details = agentcore_client.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)

            print(f"  Status: {target_details.get('status', 'unknown')}")
            print()

        return all_tools

    except Exception as e:
        print(f"❌ Error listing tools: {e}")
        return []


def verify_expected_tools(tools):
    """Verify that expected Visa B2B tools are present"""
    expected_tools = ["VirtualCardRequisition", "ProcessPayments", "GetPaymentDetails"]

    print("Verifying expected tools...")
    print()

    # Note: Tool names from OpenAPI may be prefixed with target name
    # Format: <target_name>___<operation_id>

    found_tools = []
    for expected in expected_tools:
        # Check if any tool contains the expected operation name
        matching = [t for t in tools if expected.lower() in t.lower()]
        if matching:
            print(f"✓ Found: {expected}")
            found_tools.append(expected)
        else:
            print(f"❌ Missing: {expected}")

    print()

    if len(found_tools) == len(expected_tools):
        print(f"✓ All {len(expected_tools)} expected tools found!")
        return True
    else:
        print(f"❌ Only {len(found_tools)}/{len(expected_tools)} tools found")
        return False


def main():
    """Main test function"""
    print("=" * 70)
    print("AgentCore Gateway Tool Verification")
    print("=" * 70)
    print()

    # Load configuration
    config = load_gateway_config()
    if not config:
        return

    gateway_id = config["gateway_id"]
    gateway_url = config["gateway_url"]
    region = config["gateway_region"]

    print(f"Gateway ID:  {gateway_id}")
    print(f"Gateway URL: {gateway_url}")
    print(f"Region:      {region}")
    print()

    # Initialize client
    agentcore_client = boto3.client("bedrock-agentcore-control", region_name=region)

    # List tools
    print("-" * 70)
    tools = list_gateway_tools(agentcore_client, gateway_id)

    # Verify tools
    print("-" * 70)
    verify_expected_tools(tools)

    print()
    print("=" * 70)
    print("Note: Tool names will be visible once gateway is fully initialized")
    print("This may take 1-2 minutes after gateway creation")
    print()
    print("To use these tools in your Payment Agent:")
    print("  1. Use MCPClient with SigV4 authentication")
    print("  2. Call list_tools_sync() to get available tools")
    print("  3. Invoke tools using call_tool_sync()")
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
