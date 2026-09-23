"""
Invoke a Strands + OpenAI agent deployed on AgentCore Runtime.

Usage:
    python invoke.py
    python invoke.py "What is the weather in Tokyo?"
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


def main():
    config = load_config()
    client = boto3.client("bedrock-agentcore", region_name=config["region"])

    prompts = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else ["What is the weather in Tokyo?", "What time is it?"]

    for prompt in prompts:
        print(f"─── Prompt: {prompt}")
        response = client.invoke_agent_runtime(
            agentRuntimeArn=config["runtime_arn"],
            payload=json.dumps({"prompt": prompt}).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
        body = response["response"].read().decode("utf-8")
        print(f"─── Response:\n{body}\n")


if __name__ == "__main__":
    main()
