"""
Run a batch evaluation job on the 5 Lambda invocation sessions.

Reads the time window from lambda_config.json (written by invoke.py), waits for
Gen AI spans to appear in CloudWatch, then starts a GoalSuccessRate batch
evaluation and polls until complete.

Usage:
    python evaluate.py
"""

import json
import sys
import time
from datetime import datetime, timezone

import boto3

# ── Load config ────────────────────────────────────────────────────────────────

with open("lambda_config.json") as f:
    config = json.load(f)

REGION = config["region"]
FUNCTION_NAME = config["function_name"]
SERVICE_NAME = config["service_name"]
LAMBDA_LOG_GROUP = config["lambda_log_group"]
START_TIME = config.get("eval_start_time")
END_TIME = config.get("eval_end_time")

if not START_TIME or not END_TIME:
    print("ERROR: No invocation time window found. Run invoke.py first.")
    sys.exit(1)

dp = boto3.client("bedrock-agentcore", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)

EVAL_NAME = f"lambda_obs_eval_{int(time.time()) % 100000}"

# ── Wait for spans ─────────────────────────────────────────────────────────────


def spans_are_ready(max_wait_seconds: int = 120) -> bool:
    """Poll the Lambda log group until compact Gen AI span records appear.

    lambda_agent.py includes _CWJsonSpanExporter which writes each OTel span as
    a single compact JSON line to stdout → CloudWatch Lambda log group.
    These records contain gen_ai.* attributes needed by the evaluator.
    """
    print(f"\nWaiting for Gen AI span records in {LAMBDA_LOG_GROUP} ...")
    deadline = time.time() + max_wait_seconds

    while time.time() < deadline:
        try:
            resp = logs.filter_log_events(
                logGroupName=LAMBDA_LOG_GROUP,
                startTime=START_TIME * 1000,
                endTime=END_TIME * 1000,
                filterPattern="invoke_agent",
                limit=5,
            )
            events = resp.get("events", [])
            if events:
                print(f"  Found {len(events)} invoke_agent span records in {LAMBDA_LOG_GROUP}")
                return True
        except logs.exceptions.ResourceNotFoundException:
            pass

        elapsed = int(time.time() - (deadline - max_wait_seconds))
        remaining = int(deadline - time.time())
        print(f"  No spans yet ({elapsed}s elapsed, {remaining}s remaining) — retrying in 15s...")
        time.sleep(15)

    print(f"  WARNING: No spans found after {max_wait_seconds}s. Proceeding anyway.")
    return False


# ── Batch evaluation ───────────────────────────────────────────────────────────


def start_evaluation() -> str:
    start_dt = datetime.fromtimestamp(START_TIME, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(END_TIME, tz=timezone.utc)

    print(f"\nStarting batch evaluation: {EVAL_NAME}")
    print(f"  Service:   {SERVICE_NAME}")
    print(f"  Window:    {start_dt.isoformat()}Z → {end_dt.isoformat()}Z")
    print(f"  Log group: {LAMBDA_LOG_GROUP}")
    print("  Note: Gen AI spans are written directly to the Lambda log group")
    print("        by _CWJsonSpanExporter in lambda_agent.py")

    resp = dp.start_batch_evaluation(
        batchEvaluationName=EVAL_NAME,
        evaluators=[{"evaluatorId": "Builtin.GoalSuccessRate"}],
        dataSourceConfig={
            "cloudWatchLogs": {
                "serviceNames": [SERVICE_NAME],
                "logGroupNames": [LAMBDA_LOG_GROUP],
                "filterConfig": {
                    "timeRange": {
                        "startTime": start_dt,
                        "endTime": end_dt,
                    }
                },
            }
        },
        description=f"Batch eval for {FUNCTION_NAME} — 5 Lambda invocations",
    )

    job_id = resp["batchEvaluationId"]
    print(f"  Job ID: {job_id}")
    return job_id


def poll_evaluation(job_id: str, timeout: int = 600) -> dict:
    """Poll until the evaluation job reaches a terminal state."""
    deadline = time.time() + timeout
    print(f"\nPolling evaluation job {job_id}...")

    while time.time() < deadline:
        resp = dp.get_batch_evaluation(batchEvaluationId=job_id)
        status = resp.get("status", "UNKNOWN")
        print(f"  Status: {status}")

        if status in ("COMPLETED", "FAILED", "STOPPED"):
            return resp

        time.sleep(20)

    print(f"  Timed out after {timeout}s")
    return dp.get_batch_evaluation(batchEvaluationId=job_id)


def print_results(result: dict):
    status = result.get("status")
    metrics = result.get("evaluationMetrics", {})

    print("\n" + "=" * 60)
    print(f"Evaluation: {EVAL_NAME}")
    print(f"Status:     {status}")
    print(f"Job ID:     {result.get('batchEvaluationId', 'N/A')}")

    if metrics:
        print("\nMetrics:")
        for key, val in metrics.items():
            print(f"  {key}: {val}")
    else:
        # Try CloudWatch Logs for detailed results if evaluation metrics not in response
        print("\nNote: Detailed results are in CloudWatch Logs.")
        print("  Log group: /aws/bedrock-agentcore/evaluations")
        print(f"  Job ID:    {result.get('batchEvaluationId', '')}")

    print("\nView in console:")
    print("  CloudWatch → Application Signals → Gen AI observability")
    print(f"  CloudWatch → X-Ray traces → Traces (filter by {FUNCTION_NAME})")
    print("=" * 60)


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("AgentCore batch evaluation — Strands agent in Lambda")
    print("=" * 60)

    spans_are_ready(max_wait_seconds=300)
    job_id = start_evaluation()

    # Save job ID to config
    config["evaluation_job_id"] = job_id
    with open("lambda_config.json", "w") as f:
        json.dump(config, f, indent=2)

    result = poll_evaluation(job_id)
    print_results(result)


if __name__ == "__main__":
    main()
