"""
Invoke an A2A agent deployed on AgentCore Runtime.

Sends a task in A2A format and reads the task result.

Usage:
    python invoke.py
    python invoke.py "How do I configure VPC endpoints?"
"""

import json
import sys
import uuid

import boto3


def load_config() -> dict:
    try:
        with open("runtime_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


def invoke_a2a(runtime_arn: str, message: str, region: str) -> dict:
    """Send an A2A task to the deployed agent."""
    client = boto3.client("bedrock-agentcore", region_name=region)

    # A2A task format
    a2a_payload = {
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "id": str(uuid.uuid4()),
        "params": {
            "id": str(uuid.uuid4()),
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": message}],
            },
        },
    }

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=json.dumps(a2a_payload).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )

    body = response["response"].read().decode("utf-8")
    return json.loads(body)


def main():
    config = load_config()

    prompts = (
        [" ".join(sys.argv[1:])]
        if len(sys.argv) > 1
        else [
            "How do I set up an S3 bucket with versioning?",
            "What are the best practices for IAM roles?",
        ]
    )

    print(f"Invoking A2A agent: {config['runtime_arn']}\n")

    for prompt in prompts:
        print(f"─── Task: {prompt}")
        result = invoke_a2a(config["runtime_arn"], prompt, config["region"])
        print(f"─── Result:\n{json.dumps(result, indent=2)}\n")


if __name__ == "__main__":
    main()
