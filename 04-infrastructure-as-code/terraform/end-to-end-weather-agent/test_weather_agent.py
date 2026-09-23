#!/usr/bin/env python3
"""
Weather Agent Test Script

This script tests the Weather Agent with various prompts including
weather queries, code interpreter usage, and browser tool interactions.

Usage:
    python test_weather_agent.py <agent_arn>

    agent_arn: ARN of the weather agent runtime (required)

Examples:
    # Test weather agent
    python test_weather_agent.py arn:aws:bedrock-agentcore:<region>:123456789012:runtime/weather-agent-id

    # From Terraform outputs
    python test_weather_agent.py $(terraform output -raw agent_runtime_arn)
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
                f"Invalid ARN format: {arn}\nExpected format: arn:aws:bedrock-agentcore:REGION:account:runtime/id"
            )

        region = parts[3]
        if not region:
            raise ValueError(
                f"Region not found in ARN: {arn}\nExpected format: arn:aws:bedrock-agentcore:REGION:account:runtime/id"
            )

        return region

    except IndexError:
        raise ValueError(
            f"Invalid ARN format: {arn}\nExpected format: arn:aws:bedrock-agentcore:REGION:account:runtime/id"
        )


def test_agent(client, agent_arn, test_name, prompt):
    """Test the agent with a given prompt

    Args:
        client: boto3 bedrock-agentcore client
        agent_arn: ARN of the agent runtime
        test_name: Name of the test for display
        prompt: Test prompt to send
    """
    print(f"\nTest: {test_name}")
    print(f"Prompt: '{prompt}'")
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
        print(f"\n❌ Error: {e}")
        return False


def test_weather_agent(agent_arn):
    """Test the weather agent with various scenarios

    Args:
        agent_arn: Weather agent runtime ARN
    """

    # Extract region from ARN
    try:
        region = extract_region_from_arn(agent_arn)
    except ValueError as e:
        print(f"\n❌ ERROR: {e}\n")
        sys.exit(1)

    print("\n" + "=" * 80)
    print("WEATHER AGENT TEST SUITE")
    print("=" * 80)
    print(f"\nAgent ARN: {agent_arn}")
    print(f"Region: {region}")

    # Create bedrock-agentcore client with extracted region
    agentcore_client = boto3.client("bedrock-agentcore", region_name=region)

    test_results = []

    # Test 1: Simple weather query
    print("\n" + "=" * 80)
    print("TEST 1: Simple Weather Query")
    print("=" * 80)
    result = test_agent(
        agentcore_client,
        agent_arn,
        "Basic Weather",
        "What's the weather like in San Francisco today?",
    )
    test_results.append(("Simple Weather Query", result))

    # Test 2: Complex query with tools (browser + code interpreter + memory)
    print("\n" + "=" * 80)
    print("TEST 2: Complex Query with Tools")
    print("=" * 80)
    result = test_agent(
        agentcore_client,
        agent_arn,
        "Weather Analysis with Tools",
        "Look up current weather conditions for Seattle, create a visualization of the temperature trend, and suggest outdoor activities based on the forecast.",
    )
    test_results.append(("Complex Query with Tools", result))

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
        print("  - Terraform: terraform output agent_runtime_arn")
        print("  - CloudFormation: aws cloudformation describe-stacks --stack-name <stack> --query 'Stacks[0].Outputs'")
        print("  - CDK: cdk deploy --outputs-file outputs.json")
        print("  - Console: Check Bedrock Agent Core console")
        sys.exit(1)

    agent_arn = sys.argv[1]

    # Validate ARN format
    if not agent_arn.startswith("arn:aws:bedrock-agentcore:"):
        print(f"\n❌ ERROR: Invalid ARN format: {agent_arn}")
        print("Expected format: arn:aws:bedrock-agentcore:region:account:runtime/runtime-id")
        sys.exit(1)

    # Run tests
    success = test_weather_agent(agent_arn)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
