"""
Invoke the Travel Agent deployed with AgentCore Observability.

Reads runtime_config.json written by deploy.py and sends travel queries.
Each invocation produces spans visible in CloudWatch GenAI Observability.

Usage:
    python invoke.py
    python invoke.py "What are the best beaches in Thailand in December?"
"""

import json
import sys

import boto3

# ── Load Config ────────────────────────────────────────────────────────────────


def load_config():
    try:
        with open("runtime_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        print("runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


# ── Invoke ─────────────────────────────────────────────────────────────────────


def invoke_agent(runtime_arn: str, region: str, prompt: str, session_id: str = "") -> str:
    client = boto3.client("bedrock-agentcore", region_name=region)

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode(),
    )

    # Read streaming response
    chunks = []
    if hasattr(response.get("response"), "read"):
        raw = response["response"].read()
        chunks.append(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
    else:
        for event in response.get("response", []):
            if isinstance(event, bytes):
                chunks.append(event.decode("utf-8"))
            elif isinstance(event, str):
                chunks.append(event)

    return "".join(chunks)


# ── Main ───────────────────────────────────────────────────────────────────────

SAMPLE_PROMPTS = [
    "What are the top travel destinations in Southeast Asia for a two-week trip?",
    "What is the weather like in Barcelona in October?",
    "Recommend a 5-day itinerary for Tokyo, Japan.",
]


def main():
    config = load_config()
    runtime_arn = config["runtime_arn"]
    region = config["region"]

    prompts = [sys.argv[1]] if len(sys.argv) > 1 else SAMPLE_PROMPTS

    print(f"Runtime ARN: {runtime_arn}")
    print(f"Region:      {region}")
    print()

    for i, prompt in enumerate(prompts, 1):
        print(f"[Query {i}] {prompt}")
        print("-" * 60)
        response = invoke_agent(
            runtime_arn,
            region,
            prompt,
            session_id=f"demo-session-{i:03d}-{__import__('uuid').uuid4()}",
        )
        print(response)
        print()

    print("Traces available at:")
    print("  CloudWatch > GenAI Observability > Bedrock AgentCore")


if __name__ == "__main__":
    main()
