#!/usr/bin/env python3

import boto3
import json


def test_multi_agent():
    """Test the multi-agent system"""

    # Get runtime ARNs from CloudFormation outputs
    cf_client = boto3.client("cloudformation", region_name="us-east-1")

    try:
        response = cf_client.describe_stacks(StackName="MultiAgentDemo")
        outputs = response["Stacks"][0]["Outputs"]

        agent1_arn = None
        agent2_arn = None

        for output in outputs:
            if output["OutputKey"] == "Agent1RuntimeArn":
                agent1_arn = output["OutputValue"]
            elif output["OutputKey"] == "Agent2RuntimeArn":
                agent2_arn = output["OutputValue"]

        print(f"Agent1 (Orchestrator) ARN: {agent1_arn}")
        print(f"Agent2 (Specialist) ARN: {agent2_arn}")

    except Exception as e:
        print(f"Error getting stack outputs: {e}")
        return

    # Create bedrock-agentcore client
    agentcore_client = boto3.client("bedrock-agentcore", region_name="us-east-1")

    # Test 1: Simple query (should be handled by Agent1 directly)
    print("\n" + "=" * 60)
    print("TEST 1: Simple greeting (Agent1 handles directly)")
    print("=" * 60)

    try:
        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=agent1_arn,
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": "Hello, how are you?"}),
        )

        print("Response received:")
        print(f"Content Type: {response.get('contentType', 'N/A')}")

        # Handle the response
        if response.get("contentType") == "application/json":
            response_body = response["response"].read()
            result = json.loads(response_body.decode("utf-8"))
            print(f"Result: {json.dumps(result, indent=2)}")
        else:
            print("Response body:")
            for chunk in response["response"]:
                print(chunk.decode("utf-8"))

    except Exception as e:
        print(f"Error testing Agent1 simple query: {e}")

    # Test 2: Complex query (should trigger Agent2 delegation)
    print("\n" + "=" * 60)
    print("TEST 2: Complex analysis (Agent1 delegates to Agent2)")
    print("=" * 60)

    try:
        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=agent1_arn,
            qualifier="DEFAULT",
            payload=json.dumps(
                {"prompt": "Provide a detailed analysis of the benefits and drawbacks of serverless architecture"}
            ),
        )

        print("Response received:")
        print(f"Content Type: {response.get('contentType', 'N/A')}")

        # Handle the response
        if response.get("contentType") == "application/json":
            response_body = response["response"].read()
            result = json.loads(response_body.decode("utf-8"))
            print(f"Result: {json.dumps(result, indent=2)}")
        else:
            print("Response body:")
            for chunk in response["response"]:
                print(chunk.decode("utf-8"))

    except Exception as e:
        print(f"Error testing Agent1 complex query: {e}")

    # Test 3: Direct Agent2 test
    print("\n" + "=" * 60)
    print("TEST 3: Direct Agent2 test (Specialist)")
    print("=" * 60)

    try:
        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=agent2_arn,
            qualifier="DEFAULT",
            payload=json.dumps({"prompt": "Explain quantum computing in detail"}),
        )

        print("Response received:")
        print(f"Content Type: {response.get('contentType', 'N/A')}")

        # Handle the response
        if response.get("contentType") == "application/json":
            response_body = response["response"].read()
            result = json.loads(response_body.decode("utf-8"))
            print(f"Result: {json.dumps(result, indent=2)}")
        else:
            print("Response body:")
            for chunk in response["response"]:
                print(chunk.decode("utf-8"))

    except Exception as e:
        print(f"Error testing Agent2 directly: {e}")


if __name__ == "__main__":
    test_multi_agent()
