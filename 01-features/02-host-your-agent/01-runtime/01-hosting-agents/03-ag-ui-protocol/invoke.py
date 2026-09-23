"""
Invoke an AG-UI agent and display streamed events.

Usage:
    python invoke.py
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


def invoke_agui(runtime_arn: str, message: str, region: str):
    """Send an AG-UI request and stream events."""
    client = boto3.client("bedrock-agentcore", region_name=region)

    agui_payload = {
        "threadId": str(uuid.uuid4()),
        "runId": str(uuid.uuid4()),
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": message,
            }
        ],
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": {},
    }

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=json.dumps(agui_payload).encode("utf-8"),
        contentType="application/json",
        accept="text/event-stream",
    )

    print("  Streaming AG-UI events:\n")

    # Read the SSE stream
    body = response["response"].read().decode("utf-8")
    for decoded in body.splitlines():
        if decoded.startswith("data:"):
            data = decoded[5:].strip()
            if data:
                try:
                    event = json.loads(data)
                    event_type = event.get("type", "unknown")
                    print(f"  [{event_type}]", end="")

                    if event_type == "TEXT_MESSAGE_CONTENT":
                        print(f" {event.get('delta', '')}", end="")
                    elif event_type == "TOOL_CALL_START":
                        print(f" tool={event.get('toolCallName', '')}")
                    elif event_type == "STATE_SNAPSHOT":
                        print(" state updated")
                    else:
                        print()
                except json.JSONDecodeError:
                    print(f"  [raw] {data}")

    print("\n  ─── Stream complete")


def main():
    config = load_config()
    print(f"Invoking AG-UI agent: {config['runtime_arn']}\n")

    prompts = [
        "Create a document about best practices for building AI agents with 3 sections.",
    ]

    for prompt in prompts:
        print(f"─── Request: {prompt}")
        invoke_agui(config["runtime_arn"], prompt, config["region"])
        print()


if __name__ == "__main__":
    main()
