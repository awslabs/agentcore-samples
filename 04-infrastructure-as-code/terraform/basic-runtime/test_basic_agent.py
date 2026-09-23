#!/usr/bin/env python3
"""
Basic Agent Test Script

This script tests the Basic Agent with simple conversational prompts.
The basic agent has no additional tools - just core Q&A capabilities.

Usage:
    python test_basic_agent.py <agent_arn>

    agent_arn: ARN of the basic agent runtime (required)

Examples:
    # Test basic agent
    python test_basic_agent.py arn:aws:bedrock-agentcore:<region>:123456789012:runtime/basic-agent-id

    # From Terraform outputs
    python test_basic_agent.py $(terraform output -raw agent_runtime_arn)
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
        prompt: The prompt to send to the agent

    Returns:
        bool: True if test passed, False otherwise
    """
    print(f"\n{'=' * 80}")
    print(f"TEST: {test_name}")
    print(f"{'=' * 80}\n")
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
                print(f"\n✅ Response:\n{response_content}")
            except json.JSONDecodeError:
                print(f"\n✅ Response:\n{response_text}")
        else:
            print("\n⚠️  No response content received")

        return True

    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        return False


def main():
    """Main test execution function"""
    if len(sys.argv) < 2:
        print("Error: Missing required agent ARN argument")
        print("\nUsage:")
        print(f"  {sys.argv[0]} <agent_arn>")
        print("\nExample:")
        print(f"  {sys.argv[0]} arn:aws:bedrock-agentcore:<region>:123456789012:runtime/agent-id")
        print("\nOr from Terraform:")
        print(f"  {sys.argv[0]} $(terraform output -raw agent_runtime_arn)")
        sys.exit(1)

    agent_arn = sys.argv[1]

    # Extract region from ARN
    try:
        region = extract_region_from_arn(agent_arn)
    except ValueError as e:
        print(f"\n❌ ERROR: {e}\n")
        sys.exit(1)

    print("=" * 80)
    print("BASIC AGENT TEST SUITE")
    print("=" * 80)
    print(f"\nAgent ARN: {agent_arn}")
    print(f"Region: {region}\n")

    # Initialize boto3 client with extracted region
    client = boto3.client("bedrock-agentcore", region_name=region)

    # Test cases for basic agent (no tools, just Q&A)
    tests = [
        {
            "name": "Simple Greeting",
            "prompt": "Hello! Can you introduce yourself?",
        },
        {
            "name": "Reasoning Task",
            "prompt": "Explain what cloud computing is in simple terms and list three key benefits.",
        },
    ]

    # Run all tests
    results = []
    for test in tests:
        passed = test_agent(client, agent_arn, test["name"], test["prompt"])
        results.append({"name": test["name"], "passed": passed})

    # Print summary
    print(f"\n{'=' * 80}")
    print("TEST SUMMARY")
    print("=" * 80)

    for result in results:
        status = "✅ PASSED" if result["passed"] else "❌ FAILED"
        print(f"{status} - {result['name']}")

    # Overall result
    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)

    print(f"\n{'=' * 80}")
    if passed_count == total_count:
        print("✅ ALL TESTS PASSED")
    else:
        print(f"⚠️  {passed_count}/{total_count} TESTS PASSED")
    print("=" * 80 + "\n")

    # Exit with appropriate code
    sys.exit(0 if passed_count == total_count else 1)


if __name__ == "__main__":
    main()
