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
import uuid

import boto3


def load_config() -> dict:
    try:
        with open("runtime_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


def invoke(client, arn: str, payload: dict, session_id: str) -> dict:
    """Invoke the agent and return the response.

    session_id has to be reused across calls that need to reach the same task
    registry: each distinct runtimeSessionId gets its own microVM, with its own
    memory, so a status check sent on a new session ID would land somewhere that
    never saw the task.
    """
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=json.dumps(payload).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
        runtimeSessionId=session_id,
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

    # Reused by both calls below. A new session ID would land on a different
    # microVM, which knows nothing about the task the first call started.
    # runtimeSessionId must be 33-256 characters, so a bare uuid4().hex (32) is
    # rejected — hence the prefix.
    session_id = f"weekly-report-{uuid.uuid4()}"

    # Step 1: Submit the report generation request
    print("── Submitting Report Generation Request ──")
    prompt = (
        "Generate a comprehensive weekly status report for the current week. "
        "Read all available data sources, perform analysis, generate visualizations, "
        "and compile the final report."
    )
    result = invoke(client, arn, {"prompt": prompt}, session_id)
    print(f"  Status: {result['status_code']}")
    print(f"  Session: {result['session_id']}")
    print(f"  Response: {result['body'][:500]}\n")

    # The status check needs the task ID, so stop here if the submit did not
    # return one rather than polling for a task that was never registered.
    try:
        task_id = json.loads(result["body"]).get("task_id")
    except (json.JSONDecodeError, TypeError):
        task_id = None

    if not task_id:
        print("Error: the agent returned no task_id, so its status cannot be checked.")
        print("Check CloudWatch logs for the agent's output.")
        sys.exit(1)

    print(f"  Task ID: {task_id}")

    # Step 2: Poll that same task, on that same session, until it leaves RUNNING.
    # Report generation runs for minutes, so a single fixed-delay check almost
    # always lands while the task is still in progress.
    print("\n── Polling Report Generation Status ──")
    poll_interval = 15
    timeout = 600
    deadline = time.monotonic() + timeout
    status = "RUNNING"
    body = "{}"

    while status == "RUNNING" and time.monotonic() < deadline:
        time.sleep(poll_interval)
        status_result = invoke(client, arn, {"task_id": task_id}, session_id)
        body = status_result["body"]
        try:
            status = json.loads(body).get("status", "UNKNOWN")
        except (json.JSONDecodeError, TypeError):
            status = "UNKNOWN"
        elapsed = int(timeout - (deadline - time.monotonic()))
        print(f"  [{elapsed}s] status: {status}")

    print(f"\n  Final response: {body[:1000]}\n")

    if status == "RUNNING":
        print(f"  Still running after {timeout}s. Check back later by sending")
        print(f'  {{"task_id": "{task_id}"}} on session {session_id}.')
        print("  Check CloudWatch logs for detailed progress output.")

    print("\n✓ Demo complete. Run 'python cleanup.py' to delete the runtime.")


if __name__ == "__main__":
    main()
