"""
Invoke the async data analysis agent.

The agent accepts analysis requests and processes them asynchronously
using Code Interpreter in the background.

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

    print("═══ Async Data Analysis Demo ═══")
    print(f"Runtime: {arn}\n")

    prompt = (
        "Analyze the sales data in s3://my-bucket/data.csv and create "
        "a summary report with visualizations. Calculate total revenue "
        "by product category and identify the top 5 performers."
    )

    print(f"Prompt: {prompt}\n")
    print("Sending request (analysis runs asynchronously)...\n")

    result = invoke(client, arn, prompt)
    print(f"  Status: {result['status_code']}")
    print(f"  Session: {result['session_id']}")
    print(f"  Response:\n{result['body']}\n")

    print("The agent is running the analysis in the background.")
    print("Use get_task_results with the task ID to retrieve results.")
    print("\n✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
