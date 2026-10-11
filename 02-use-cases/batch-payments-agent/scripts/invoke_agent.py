"""Invoke the deployed DeFi Payments Agent on AgentCore Runtime.

Usage:
    python scripts/invoke_agent.py "What's the price of ETH?"
    python scripts/invoke_agent.py --runtime-arn <arn> "Send batch payment"
"""

import argparse
import json
import uuid

import boto3


def invoke(prompt: str, runtime_arn: str | None = None, region: str = "us-east-1"):
    """Invoke the agent on AgentCore Runtime."""
    client = boto3.client("bedrock-agentcore-runtime", region_name=region)

    if not runtime_arn:
        sts = boto3.client("sts")
        account_id = sts.get_caller_identity()["Account"]
        runtime_arn = (
            f"arn:aws:bedrock-agentcore:{region}:{account_id}"
            f":runtime/batch-payments-agent"
        )

    session_id = str(uuid.uuid4())

    print(f"Runtime: {runtime_arn}")
    print(f"Session: {session_id}")
    print(f"Prompt:  {prompt}\n")

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        sessionId=session_id,
        input={"prompt": prompt},
    )

    # Process streaming response
    for event in response.get("output", {}).get("events", []):
        if "message" in event:
            print(event["message"].get("text", ""))

    print(f"\nSession ID: {session_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Invoke the DeFi Payments Agent")
    parser.add_argument("prompt", type=str, help="Prompt to send to the agent")
    parser.add_argument("--runtime-arn", type=str, help="AgentCore Runtime ARN")
    parser.add_argument("--region", type=str, default="us-east-1")
    args = parser.parse_args()

    invoke(args.prompt, args.runtime_arn, args.region)
