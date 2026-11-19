#!/usr/bin/env python3
"""
Multi-Agent System Test Script

This script tests a multi-agent system with Agent-to-Agent (A2A) communication.
It can work with agents deployed via any method (Terraform, CloudFormation, CDK, manual).

Usage:
    python test_multi_agent.py <orchestrator_arn> [specialist_arn]
    
    orchestrator_arn: ARN of the orchestrator agent (required)
    specialist_arn: ARN of the specialist agent (optional, for independent testing)

Examples:
    # Test orchestrator with A2A communication
    python test_multi_agent.py arn:aws:bedrock-agentcore:<region>:123456789012:runtime/orchestrator-id
    
    # Test both agents independently
    python test_multi_agent.py \\
        arn:aws:bedrock-agentcore:<region>:123456789012:runtime/orchestrator-id \\
        arn:aws:bedrock-agentcore:<region>:123456789012:runtime/specialist-id
"""

import boto3
import json
import sys


def extract_region_from_arn(arn):
    """Extract AWS region from agent runtime ARN.

    ARN format: arn:aws:bedrock-agentcore:REGION:account:runtime/id

    Args:
        arn: Agent runtime ARN string

    Returns:
        str: AWS region code

    Raises:
        ValueError: If ARN format is invalid or region cannot be extracted
    """
    try:
        parts = arn.split(":")
        if len(parts) < 4:
            raise ValueError(
                f"Invalid ARN format: {arn}\n"
                f"Expected format: arn:aws:bedrock-agentcore:REGION:account:runtime/id"
            )

        region = parts[3]
        if not region:
            raise ValueError(
                f"Region not found in ARN: {arn}\n"
                f"Expected format: arn:aws:bedrock-agentcore:REGION:account:runtime/id"
            )

        return region

    except IndexError:
        raise ValueError(
            f"Invalid ARN format: {arn}\n"
            f"Expected format: arn:aws:bedrock-agentcore:REGION:account:runtime/id"
        )


def test_agent(client, agent_arn, agent_name, prompt):
    """Test a single agent with a given prompt

    Args:
        client: boto3 bedrock-agentcore client
        agent_arn: ARN of the agent runtime
        agent_name: Name for display purposes
        prompt: Test prompt to send
    """
    print(f"\nPrompt: '{prompt}'")
    print("-" * 80)

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": prompt}),
        )

        print(f"Status: {response['ResponseMetadata']['HTTPStatusCode']}")
        print(f"Content Type: {response.get('contentType', 'N/A')}")

        # Read the streaming response body
        response_text = ""
        if "response" in response:
            response_body = response["response"].read()
            response_text = response_body.decode("utf-8")

        if response_text:
            try:
                result = json.loads(response_text)
                response_content = result.get("response", response_text)
                # Truncate long responses for readability
                if len(response_content) > 500:
                    print(f"\n✅ Response:\n{response_content[:500]}...")
                    print("\n[Response truncated for display]")
                else:
                    print(f"\n✅ Response:\n{response_content}")
            except json.JSONDecodeError:
                if len(response_text) > 500:
                    print(f"\n✅ Response:\n{response_text[:500]}...")
                else:
                    print(f"\n✅ Response:\n{response_text}")
        else:
            print("\n⚠️  No response content received")

        return True

    except Exception as e:
        print(f"\n❌ Error testing {agent_name}: {e}")
        return False


def test_multi_agent(orchestrator_arn, specialist_arn=None):
    """Test the multi-agent system

    Args:
        orchestrator_arn: Orchestrator agent runtime ARN (required)
        specialist_arn: Specialist agent runtime ARN (optional)
    """

    # Extract region from orchestrator ARN
    try:
        region = extract_region_from_arn(orchestrator_arn)
    except ValueError as e:
        print(f"\n❌ ERROR: {e}\n")
        sys.exit(1)

    print("\n" + "=" * 80)
    print("MULTI-AGENT SYSTEM TEST")
    print("=" * 80)
    print(f"\nOrchestrator Agent ARN: {orchestrator_arn}")
    if specialist_arn:
        print(f"Specialist Agent ARN: {specialist_arn}")
    else:
        print("Specialist Agent: Not provided (will test Orchestrator only)")
    print(f"Region: {region}")

    # Create bedrock-agentcore client with extracted region
    agentcore_client = boto3.client("bedrock-agentcore", region_name=region)

    test_results = []

    # Test 1: Simple query to Orchestrator
    print("\n" + "=" * 80)
    print("TEST 1: Simple Query (Orchestrator)")
    print("=" * 80)
    result = test_agent(
        agentcore_client,
        orchestrator_arn,
        "Orchestrator",
        "Hello! Can you introduce yourself and your capabilities?",
    )
    test_results.append(("Simple Query", result))

    # Test 2: Complex query triggering A2A communication
    print("\n" + "=" * 80)
    print("TEST 2: Complex Query with A2A Communication")
    print("=" * 80)
    result = test_agent(
        agentcore_client,
        orchestrator_arn,
        "Orchestrator",
        "I need expert analysis. Please coordinate with the specialist agent to provide a comprehensive explanation of cloud computing architectures and best practices.",
    )
    test_results.append(("A2A Communication Test", result))

    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    all_passed = True
    for test_name, passed in test_results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status} - {test_name}")
        if not passed:
            all_passed = False

    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ALL TESTS PASSED")
    else:
        print("⚠️  SOME TESTS FAILED")
    print("=" * 80 + "\n")

    return all_passed


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n❌ ERROR: Agent runtime ARN is required")
        print("\nTo get your agent ARN:")
        print("  - Terraform: terraform output orchestrator_runtime_arn")
        print(
            "  - CloudFormation: aws cloudformation describe-stacks --stack-name <stack> --query 'Stacks[0].Outputs'"
        )
        print("  - CDK: cdk deploy --outputs-file outputs.json")
        print("  - Console: Check Bedrock Agent Core console")
        sys.exit(1)

    orchestrator_arn = sys.argv[1]
    specialist_arn = sys.argv[2] if len(sys.argv) > 2 else None

    # Validate ARN format
    if not orchestrator_arn.startswith("arn:aws:bedrock-agentcore:"):
        print(f"\n❌ ERROR: Invalid ARN format for orchestrator: {orchestrator_arn}")
        print(
            "Expected format: arn:aws:bedrock-agentcore:region:account:runtime/runtime-id"
        )
        sys.exit(1)

    if specialist_arn and not specialist_arn.startswith("arn:aws:bedrock-agentcore:"):
        print(f"\n❌ ERROR: Invalid ARN format for specialist: {specialist_arn}")
        print(
            "Expected format: arn:aws:bedrock-agentcore:region:account:runtime/runtime-id"
        )
        sys.exit(1)

    # Run tests
    success = test_multi_agent(orchestrator_arn, specialist_arn)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
