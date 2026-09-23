"""
Invoke the VPC-connected agent deployed on AgentCore Runtime.

Reads the runtime ARN from runtime_config.json (created by deploy.py)
and sends a sample payload to the agent, which forwards it to the
Fargate echo service over the private VPC network.

Usage:
    python invoke.py
    python invoke.py "hello from the VPC"
"""

import json
import sys

import boto3


def load_config() -> dict:
    try:
        with open("runtime_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


def invoke(runtime_arn: str, payload: dict, region: str) -> str:
    """Send a payload to the deployed agent and return the response."""
    client = boto3.client("bedrock-agentcore", region_name=region)

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=json.dumps(payload).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )

    body = response["response"].read().decode("utf-8")
    session_id = response.get("runtimeSessionId", "N/A")

    print(f"  Session ID: {session_id}")
    print(f"  Status:     {response.get('statusCode', 'N/A')}")

    return body


def main():
    config = load_config()
    runtime_arn = config["runtime_arn"]
    region = config["region"]

    # Use CLI argument or default message
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
    else:
        message = "hello from AgentCore VPC mode"

    payload = {"message": message}

    print(f"Invoking agent: {runtime_arn}")
    print(f"  Payload: {json.dumps(payload)}\n")

    response = invoke(runtime_arn, payload, region)
    print(f"─── Response:\n{response}\n")


if __name__ == "__main__":
    main()
