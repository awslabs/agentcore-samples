"""
Invoke a Strands + Bedrock agent deployed on AgentCore Runtime.

Reads the runtime ARN from runtime_config.json (created by deploy.py)
and sends sample prompts using the bedrock-agentcore data plane API.

Usage:
    python invoke.py
    python invoke.py "What time is it?"
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


def invoke(runtime_arn: str, prompt: str, region: str) -> str:
    """Send a prompt to the deployed agent and return the response."""
    client = boto3.client("bedrock-agentcore", region_name=region)

    payload = json.dumps({"prompt": prompt}).encode("utf-8")

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=payload,
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

    # Use CLI argument or default prompts
    if len(sys.argv) > 1:
        prompts = [" ".join(sys.argv[1:])]
    else:
        prompts = [
            "What is the weather in Seattle?",
            "What time is it right now?",
            "Compare the weather in Seattle and Miami.",
        ]

    print(f"Invoking agent: {runtime_arn}\n")

    for prompt in prompts:
        print(f"─── Prompt: {prompt}")
        response = invoke(runtime_arn, prompt, region)
        print(f"─── Response:\n{response}\n")


if __name__ == "__main__":
    main()
