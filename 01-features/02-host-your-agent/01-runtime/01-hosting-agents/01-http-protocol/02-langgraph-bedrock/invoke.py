"""
Invoke a LangGraph + Bedrock agent deployed on AgentCore Runtime.

Usage:
    python invoke.py
    python invoke.py "What is 123 * 456?"
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
    client = boto3.client("bedrock-agentcore", region_name=region)

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )

    body = response["response"].read().decode("utf-8")
    print(f"  Session: {response.get('runtimeSessionId', 'N/A')}")
    return body


def main():
    config = load_config()

    prompts = (
        [" ".join(sys.argv[1:])]
        if len(sys.argv) > 1
        else [
            "What is 25 * 17 + 42?",
            "Calculate the square root of 144 plus 58.",
        ]
    )

    print(f"Invoking agent: {config['runtime_arn']}\n")
    for prompt in prompts:
        print(f"─── Prompt: {prompt}")
        response = invoke(config["runtime_arn"], prompt, config["region"])
        print(f"─── Response:\n{response}\n")


if __name__ == "__main__":
    main()
