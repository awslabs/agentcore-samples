"""
Invoke an agent with streaming (SSE) response.

Demonstrates reading the response stream incrementally
as the agent generates tokens.

Usage:
    python invoke.py
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


def invoke_streaming(runtime_arn: str, prompt: str, region: str):
    """Invoke the agent and stream the response."""
    client = boto3.client("bedrock-agentcore", region_name=region)

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        contentType="application/json",
        accept="text/event-stream",  # Request SSE streaming
    )

    print(f"  Session: {response.get('runtimeSessionId', 'N/A')}")
    print("  Streaming response:\n")

    # Read SSE events or plain response
    body = response["response"].read()
    decoded = body.decode("utf-8") if isinstance(body, bytes) else body

    found_sse = False
    for line in decoded.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk:
                print(chunk, end="", flush=True)
                found_sse = True

    if not found_sse:
        # Agent returned plain JSON/text
        print(decoded, end="", flush=True)

    print("\n")


def main():
    config = load_config()

    prompts = (
        [" ".join(sys.argv[1:])]
        if len(sys.argv) > 1
        else [
            "Write a detailed explanation of how neural networks learn, covering backpropagation and gradient descent.",
        ]
    )

    print(f"Invoking agent (streaming): {config['runtime_arn']}\n")

    for prompt in prompts:
        print(f"─── Prompt: {prompt}")
        invoke_streaming(config["runtime_arn"], prompt, config["region"])


if __name__ == "__main__":
    main()
