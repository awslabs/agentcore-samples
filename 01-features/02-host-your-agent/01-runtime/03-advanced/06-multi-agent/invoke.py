"""
Multi-Agent Demo — send questions to the orchestrator and watch it route.

The orchestrator decides whether to forward to the tech agent or HR agent
based on the question content.

Usage:
    python deploy.py   # deploy all three agents first
    python invoke.py   # run this demo
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


def invoke(client, arn: str, prompt: str) -> str:
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    return response["response"].read().decode("utf-8")


def main():
    config = load_config()
    orch_arn = config["orchestrator_arn"]
    region = config["region"]
    client = boto3.client("bedrock-agentcore", region_name=region)

    print("═══ Multi-Agent Orchestration Demo ═══")
    print(f"Orchestrator: {orch_arn}\n")

    questions = [
        # Should route to tech agent
        ("Tech", "I'm getting a 403 error when calling the S3 API. How do I fix it?"),
        # Should route to HR agent
        ("HR", "What is the company's 401k matching policy?"),
        # Should route to HR agent
        ("HR", "How many PTO days do I get per year?"),
        # Should route to tech agent
        ("Tech", "How do I set up a Python virtual environment?"),
        # General — orchestrator answers directly
        ("General", "What day is it today?"),
    ]

    for expected_route, question in questions:
        print(f"── [{expected_route}] {question}")
        response = invoke(client, orch_arn, question)
        # Truncate long responses for readability
        display = response[:400] + "..." if len(response) > 400 else response
        print(f"   → {display}\n")

    print("✓ Demo complete. Run 'python cleanup.py' to delete all agents.")


if __name__ == "__main__":
    main()
