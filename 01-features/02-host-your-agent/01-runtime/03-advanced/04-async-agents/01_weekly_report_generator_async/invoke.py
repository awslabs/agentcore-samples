"""
Invoke the weekly report generator async agent.

The agent processes requests asynchronously — it accepts a report generation
request, starts processing in the background, and returns a task ID immediately.
You can then check the status of the report generation.

Usage:
    python deploy.py   # deploy first
    python invoke.py   # run this demo
"""

import json
import sys
import time

import boto3


def load_config() -> dict:
    try:
        with open("runtime_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


def invoke(client, arn: str, prompt: str) -> dict:
    """Invoke the agent and return the response."""
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
    }


def main():
    config = load_config()
    arn = config["runtime_arn"]
    region = config["region"]
    client = boto3.client("bedrock-agentcore", region_name=region)

    print("═══ Weekly Report Generator (Async) ═══")
    print(f"Runtime: {arn}\n")

    # Step 1: Submit the report generation request
    print("── Submitting Report Generation Request ──")
    prompt = (
        "Generate a comprehensive weekly status report for the current week. "
        "Read all available data sources, perform analysis, generate visualizations, "
        "and compile the final report."
    )
    result = invoke(client, arn, prompt)
    print(f"  Status: {result['status_code']}")
    print(f"  Session: {result['session_id']}")
    print(f"  Response: {result['body'][:500]}\n")

    # Parse the task ID from the response if available
    try:
        resp_data = json.loads(result["body"])
        task_id = resp_data.get("task_id", "N/A")
        print(f"  Task ID: {task_id}")
    except (json.JSONDecodeError, TypeError):
        task_id = "N/A"

    # Step 2: Wait and check the status
    print("\n── Checking Report Generation Status (after 10s) ──")
    time.sleep(10)
    status_result = invoke(client, arn, "What is the status of the report generation?")
    print(f"  Status: {status_result['status_code']}")
    print(f"  Response: {status_result['body'][:500]}\n")

    print("The agent processes the report asynchronously.")
    print("Check CloudWatch logs for detailed progress output.")
    print("\n✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
