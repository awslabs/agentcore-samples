"""
Invoke the middleware agent and observe the middleware effects.

The agent has two middleware layers:
1. ObservabilityMiddleware — logs request/response timing, adds baggage metadata
2. ErrorHandlingMiddleware — catches errors, adds correlation IDs

Usage:
    python deploy.py   # deploy first
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


def invoke(client, arn: str, prompt: str) -> dict:
    """Invoke the agent and return the full response with headers."""
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    body = response["response"].read().decode("utf-8")
    return {
        "body": body,
        "session_id": response.get("runtimeSessionId", "N/A"),
        "status_code": response.get("statusCode", "N/A"),
        # Baggage from middleware is returned in the response
        "baggage": response.get("baggage", ""),
    }


def main():
    config = load_config()
    arn = config["runtime_arn"]
    region = config["region"]
    client = boto3.client("bedrock-agentcore", region_name=region)

    print("═══ Middleware Demo ═══")
    print(f"Runtime: {arn}\n")
    print("This agent has two middleware layers:")
    print("  1. ObservabilityMiddleware — timing + logging")
    print("  2. ErrorHandlingMiddleware — correlation IDs + error formatting\n")

    # Normal request — both middleware layers process it
    print("── Normal Request ──")
    result = invoke(client, arn, "What is 2 + 2?")
    print(f"  Status: {result['status_code']}")
    print(f"  Session: {result['session_id']}")
    if result["baggage"]:
        print(f"  Baggage (from middleware): {result['baggage']}")
    print(f"  Response: {result['body'][:300]}\n")

    # Another request to see timing
    print("── Second Request (same session shows faster response) ──")
    result = invoke(client, arn, "Tell me a short joke.")
    print(f"  Status: {result['status_code']}")
    if result["baggage"]:
        print(f"  Baggage: {result['baggage']}")
    print(f"  Response: {result['body'][:300]}\n")

    print("Check CloudWatch logs for detailed middleware output:")
    print("  - Request/response timing from ObservabilityMiddleware")
    print("  - Correlation IDs from ErrorHandlingMiddleware")
    print("  - x-process-time and x-correlation-id headers")

    print("\n✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
