"""
Strale Compliance Agent — AgentCore + Strale MCP Integration

Demonstrates connecting Strale's MCP server to Amazon Bedrock AgentCore
Gateway for trust-sensitive data operations: IBAN validation, company
lookups, and sanctions screening.

Prerequisites:
    - AWS account with Bedrock AgentCore access (us-east-1)
    - Strale API key (free at https://strale.dev/signup)
    - Python 3.10+

Install:
    pip install boto3 strands-agents mcp

Usage:
    export AWS_REGION=us-east-1
    export STRALE_API_KEY=sk_live_your_key_here
    python agent-example.py
"""

import json
import os
import sys

import boto3


# ── Configuration ──────────────────────────────────────────────────────────────

REGION = os.environ.get("AWS_REGION", "us-east-1")
STRALE_API_KEY = os.environ.get("STRALE_API_KEY", "")
GATEWAY_NAME = "strale-compliance-gateway"
TARGET_NAME = "strale-mcp-target"


# ── Step 1: Create Gateway ─────────────────────────────────────────────────────

def create_gateway(client: "boto3.client") -> tuple[str, str]:
    """Create an AgentCore MCP Gateway with semantic search enabled."""
    print("Creating AgentCore Gateway...")

    # Get or create IAM role for the gateway
    iam = boto3.client("iam", region_name=REGION)
    role_name = f"agentcore-{GATEWAY_NAME}-role"

    try:
        role = iam.get_role(RoleName=role_name)
        role_arn = role["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Gateway role for Strale integration",
        )
        role_arn = role["Role"]["Arn"]
        print(f"  Created IAM role: {role_arn}")

    response = client.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        protocolType="MCP",
        protocolConfiguration={
            "mcp": {
                "supportedVersions": ["2025-03-26"],
                "searchType": "SEMANTIC",
            }
        },
        description="Gateway with Strale trust & compliance capabilities",
    )

    gateway_id = response["gatewayId"]
    gateway_url = response["gatewayUrl"]
    print(f"  Gateway created: {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")
    return gateway_id, gateway_url


# ── Step 2: Register Strale MCP Server ─────────────────────────────────────────

def register_strale_target(
    client: "boto3.client", gateway_id: str
) -> str:
    """Register Strale as a remote MCP server target."""
    print("Registering Strale MCP server...")

    # Create API key credential provider for Strale auth
    provider_response = client.create_api_key_credential_provider(
        name="strale-api-key",
        apiKey=STRALE_API_KEY,
    )
    provider_arn = provider_response["credentialProviderArn"]
    print(f"  Credential provider: {provider_arn}")

    # Register the MCP server target
    target_response = client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=TARGET_NAME,
        targetConfiguration={
            "mcp": {
                "mcpServer": {
                    "endpoint": "https://api.strale.io/mcp",
                }
            }
        },
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "API_KEY",
                "credentialProvider": {
                    "apiKeyCredentialProvider": {
                        "providerArn": provider_arn,
                        "credentialParameterName": "Authorization",
                        "credentialPrefix": "Bearer ",
                        "credentialLocation": "HEADER",
                    }
                },
            }
        ],
    )

    target_id = target_response["targetId"]
    print(f"  Target registered: {target_id}")
    print("  Strale tools are now discoverable via the gateway.")
    return target_id


# ── Step 3: Use the Agent ──────────────────────────────────────────────────────

def run_agent(gateway_url: str, access_token: str) -> None:
    """Run a compliance agent that uses Strale tools via the gateway."""
    try:
        from mcp.client.streamable_http import streamablehttp_client
        from strands import Agent
        from strands.models import BedrockModel
        from strands.tools.mcp.mcp_client import MCPClient
    except ImportError:
        print("\nInstall agent dependencies: pip install strands-agents mcp")
        print("Then re-run this script.")
        return

    print("\nConnecting to gateway and discovering tools...")

    def create_transport():
        return streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    mcp_client = MCPClient(create_transport)

    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        temperature=0.3,
        max_tokens=2000,
    )

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        tool_names = [t.name for t in tools]
        strale_tools = [n for n in tool_names if n.startswith("strale_")]
        print(f"  Discovered {len(tools)} tools, {len(strale_tools)} from Strale")

        agent = Agent(model=model, tools=tools)

        # Example 1: Validate an IBAN (free, no credits used)
        print("\n--- Example 1: IBAN Validation (free) ---")
        result = agent(
            "Validate this IBAN: DE89370400440532013000. "
            "Use the strale_execute tool with slug 'iban-validate'."
        )
        print(result)

        # Example 2: Look up a Swedish company
        print("\n--- Example 2: Company Lookup ---")
        result = agent(
            "Look up the Swedish company with org number 5591674668 using Strale. "
            "First search for the right capability, then execute it."
        )
        print(result)

        # Example 3: Check quality before calling
        print("\n--- Example 3: Quality Check ---")
        result = agent(
            "Before running a sanctions check, use strale_trust_profile "
            "to check the quality score for 'pep-check'. "
            "Is it reliable enough for production use?"
        )
        print(result)


# ── Step 4: Cleanup ────────────────────────────────────────────────────────────

def cleanup(client: "boto3.client", gateway_id: str, target_id: str) -> None:
    """Remove the gateway and target."""
    print("\nCleaning up...")
    try:
        client.delete_gateway_target(
            gatewayIdentifier=gateway_id, targetIdentifier=target_id
        )
        print(f"  Deleted target: {target_id}")
    except Exception as e:
        print(f"  Target deletion: {e}")

    try:
        client.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  Deleted gateway: {gateway_id}")
    except Exception as e:
        print(f"  Gateway deletion: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if not STRALE_API_KEY:
        print("Set STRALE_API_KEY environment variable.")
        print("Get a free key at https://strale.dev/signup")
        sys.exit(1)

    client = boto3.client("bedrock-agentcore-control", region_name=REGION)

    gateway_id, gateway_url = create_gateway(client)
    target_id = register_strale_target(client, gateway_id)

    # In production, you'd get the access token from Cognito/OIDC.
    # For this example, we use a placeholder.
    print("\nTo run the agent, obtain an access token from your gateway's")
    print("OIDC provider and call run_agent(gateway_url, access_token).")
    print(f"\nGateway URL: {gateway_url}")

    input("\nPress Enter to clean up resources...")
    cleanup(client, gateway_id, target_id)


if __name__ == "__main__":
    main()
