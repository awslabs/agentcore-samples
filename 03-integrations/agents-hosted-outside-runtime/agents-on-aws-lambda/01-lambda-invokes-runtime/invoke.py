"""
Invoke the Lambda function that calls the AgentCore Runtime MCP agent.

Reads runtime_config.json written by deploy.py.

Usage:
    python invoke.py
    python invoke.py --prompt "What is AWS CDK?"
"""

import argparse
import json
import time

import boto3

TEST_PROMPTS = [
    "What is Amazon Bedrock? Answer in one sentence.",
    "What are 2 AWS compute services?",
    "What is Amazon S3 used for? Be brief.",
]


def invoke_lambda(lambda_client, function_name: str, prompt: str) -> dict:
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"prompt": prompt}),
    )
    return json.loads(response["Payload"].read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", help="Single prompt to send")
    args = parser.parse_args()

    with open("runtime_config.json") as f:
        config = json.load(f)

    lambda_client = boto3.client("lambda", region_name=config["region"])
    function_name = config["lambda_function_name"]

    prompts = [args.prompt] if args.prompt else TEST_PROMPTS

    print(f"Invoking Lambda: {function_name}")
    print()

    for i, prompt in enumerate(prompts, 1):
        print(f"{'=' * 60}")
        print(f"Prompt {i}: {prompt}")
        print("=" * 60)

        result = invoke_lambda(lambda_client, function_name, prompt)

        if "FunctionError" in result:
            print(f"Lambda error: {result}")
        elif result.get("statusCode") == 200:
            body = json.loads(result["body"])
            print(f"Session ID: {body.get('sessionId', 'N/A')}")
            print(f"\nResponse:\n{body.get('response', '')}")
        else:
            body = json.loads(result.get("body", "{}"))
            print(f"Error ({result.get('statusCode')}): {body}")

        if i < len(prompts):
            time.sleep(2)

    print()
    print("View traces: CloudWatch -> Gen AI Observability -> Agents")


if __name__ == "__main__":
    main()
