"""Evaluations — run batch evaluation against harness session traces."""

import time
import uuid

import boto3

from resources import REGION

EVALUATOR_IDS = [
    "Builtin.InstructionFollowing",
    "Builtin.Helpfulness",
    "Builtin.Correctness",
    "Builtin.Faithfulness",
    "Builtin.ResponseRelevance",
    "Builtin.Coherence",
    "Builtin.Conciseness",
    "Builtin.Refusal",
]


def _discover_log_group(harness_name: str) -> str | None:
    """Find the CloudWatch log group for a harness by prefix search."""
    logs = boto3.client("logs", region_name=REGION)
    prefix = f"/aws/bedrock-agentcore/runtimes/harness_{harness_name}-"
    resp = logs.describe_log_groups(logGroupNamePrefix=prefix, limit=5)
    groups = resp.get("logGroups", [])
    if groups:
        # Return the most recently created one
        groups.sort(key=lambda g: g.get("creationTime", 0), reverse=True)
        return groups[0]["logGroupName"]
    return None


def run_batch_evaluation(harness_id: str, harness_name: str = None) -> dict:
    """Start a batch evaluation job and poll until complete. Returns results."""
    client = boto3.client("bedrock-agentcore", region_name=REGION)

    if not hasattr(client, "start_batch_evaluation"):
        return {
            "error": (
                "start_batch_evaluation is not available in your boto3 version. "
                "Please upgrade: pip install 'boto3>=1.43.27'"
            ),
            "scores": [],
        }

    # Discover the log group dynamically (it has a random suffix)
    log_group = None
    if harness_name:
        log_group = _discover_log_group(harness_name)
    if not log_group:
        # Fallback: try with harness_id directly
        log_group = _discover_log_group(harness_id)
    if not log_group:
        return {"error": f"Could not find log group for harness {harness_name or harness_id}", "scores": []}

    # Service name format: harness_{name}.DEFAULT (without the random suffix)
    # Log group: harness_WeatherAgent_537bb0c9-d9RslKDml1-DEFAULT
    # Service:   harness_WeatherAgent_537bb0c9.DEFAULT
    log_group_basename = log_group.split("/")[-1]  # harness_WeatherAgent_537bb0c9-d9RslKDml1-DEFAULT
    parts = log_group_basename.rsplit("-", 2)  # ['harness_WeatherAgent_537bb0c9', 'd9RslKDml1', 'DEFAULT']
    service_name = f"{parts[0]}.DEFAULT" if len(parts) >= 3 else log_group_basename.replace("-DEFAULT", ".DEFAULT")

    batch_name = f"weather_eval_{uuid.uuid4().hex[:8]}"

    try:
        resp = client.start_batch_evaluation(
            batchEvaluationName=batch_name,
            evaluators=[{"evaluatorId": eid} for eid in EVALUATOR_IDS],
            dataSourceConfig={
                "cloudWatchLogs": {
                    "serviceNames": [service_name],
                    "logGroupNames": [log_group],
                }
            },
        )
    except Exception as e:
        return {"error": str(e), "scores": []}

    batch_id = resp["batchEvaluationId"]

    # Poll until complete (timeout after 5 minutes)
    deadline = time.monotonic() + 300
    status = "PENDING"
    while time.monotonic() < deadline:
        time.sleep(10)
        try:
            result = client.get_batch_evaluation(batchEvaluationId=batch_id)
            status = result.get("status", "UNKNOWN")
            if status in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                break
        except Exception:
            pass

    if status not in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
        # Try to get failure details
        failure_reason = ""
        try:
            failure_reason = result.get("failureReasons", result.get("statusReason", ""))
            if not failure_reason:
                # Check evaluationResults for per-session errors
                eval_res = result.get("evaluationResults", {})
                failed_count = eval_res.get("numberOfSessionsFailed", 0)
                completed_count = eval_res.get("numberOfSessionsCompleted", 0)
                if failed_count > 0:
                    failure_reason = f"{failed_count} session(s) failed, {completed_count} completed"
        except Exception:
            pass
        error_msg = f"Evaluation did not complete (status: {status})"
        if failure_reason:
            error_msg += f". {failure_reason}"
        print(f"[eval] Failed: {error_msg}")
        print(f"[eval] Full response: {result}")
        return {
            "batch_id": batch_id,
            "batch_name": batch_name,
            "status": status,
            "error": error_msg,
            "scores": [],
        }

    # Extract evaluator results
    scores = []
    eval_results = result.get("evaluationResults", {})
    summaries = eval_results.get("evaluatorSummaries", [])

    for summary in summaries:
        eid = summary.get("evaluatorId", "")
        stats = summary.get("statistics", {})
        avg_score = stats.get("averageScore")
        evaluated = summary.get("totalEvaluated", 0)

        name = eid.replace("Builtin.", "") if eid.startswith("Builtin.") else eid
        scores.append({
            "evaluator": name,
            "score": avg_score,
            "evaluated_sessions": evaluated,
        })

    return {
        "batch_id": batch_id,
        "batch_name": batch_name,
        "status": status,
        "total_sessions": eval_results.get("numberOfSessionsCompleted", 0),
        "scores": scores,
    }
