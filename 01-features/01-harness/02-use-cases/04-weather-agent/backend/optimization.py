"""Optimization — generate system prompt recommendations from agent traces."""

import time
import uuid
from datetime import datetime, timedelta, timezone

import boto3

from resources import REGION
from agent import SYSTEM_PROMPT


def _discover_log_group_arn(harness_name: str) -> tuple[str, str] | None:
    """Find the log group ARN and service name for a harness."""
    logs = boto3.client("logs", region_name=REGION)
    prefix = f"/aws/bedrock-agentcore/runtimes/harness_{harness_name}-"
    resp = logs.describe_log_groups(logGroupNamePrefix=prefix, limit=5)
    groups = resp.get("logGroups", [])
    if not groups:
        return None
    groups.sort(key=lambda g: g.get("creationTime", 0), reverse=True)
    log_group = groups[0]
    log_group_arn = log_group["arn"]
    log_group_name = log_group["logGroupName"]

    basename = log_group_name.split("/")[-1]
    parts = basename.rsplit("-", 2)
    service_name = f"{parts[0]}.DEFAULT" if len(parts) >= 3 else basename.replace("-DEFAULT", ".DEFAULT")

    return log_group_arn, service_name


def run_optimization(harness_name: str, evaluator: str = "Builtin.GoalSuccessRate") -> dict:
    """Run a system prompt recommendation and return the result."""
    client = boto3.client("bedrock-agentcore", region_name=REGION)

    # Discover log group
    result = _discover_log_group_arn(harness_name)
    if not result:
        return {"error": "Could not find log group. Send some chat messages first.", "status": "FAILED"}

    log_group_arn, service_name = result

    now = datetime.now(timezone.utc)
    start_time = now - timedelta(days=7)
    rec_name = f"weather_rec_{uuid.uuid4().hex[:8]}"

    # Start recommendation
    try:
        resp = client.start_recommendation(
            name=rec_name,
            type="SYSTEM_PROMPT_RECOMMENDATION",
            recommendationConfig={
                "systemPromptRecommendationConfig": {
                    "systemPrompt": {"text": SYSTEM_PROMPT},
                    "agentTraces": {
                        "cloudwatchLogs": {
                            "logGroupArns": [log_group_arn],
                            "serviceNames": [service_name],
                            "startTime": start_time,
                            "endTime": now,
                        }
                    },
                    "evaluationConfig": {
                        "evaluators": [
                            {"evaluatorArn": f"arn:aws:bedrock-agentcore:::evaluator/{evaluator}"}
                        ]
                    },
                }
            },
            clientToken=str(uuid.uuid4()),
        )
    except Exception as e:
        return {"error": str(e), "status": "FAILED"}

    rec_id = resp["recommendationId"]

    # Poll for completion (timeout 5 minutes)
    status = "PENDING"
    for _ in range(30):
        time.sleep(10)
        try:
            rec = client.get_recommendation(recommendationId=rec_id)
            status = rec.get("status", "UNKNOWN")
            if status in ("COMPLETED", "FAILED"):
                break
        except Exception:
            pass

    if status != "COMPLETED":
        error_msg = ""
        if status == "FAILED":
            rec_result = rec.get("recommendationResult", {}).get(
                "systemPromptRecommendationResult", {}
            )
            error_msg = rec_result.get("errorMessage", "Unknown error")
        return {
            "status": status,
            "error": error_msg or f"Recommendation did not complete (status: {status})",
            "recommendation_name": rec_name,
        }

    # Extract result
    rec_result = rec.get("recommendationResult", {}).get(
        "systemPromptRecommendationResult", {}
    )

    return {
        "status": "COMPLETED",
        "recommendation_name": rec_name,
        "recommendation_id": rec_id,
        "evaluator": evaluator,
        "current_prompt": SYSTEM_PROMPT,
        "recommended_prompt": rec_result.get("recommendedSystemPrompt", ""),
        "explanation": rec_result.get("explanation", ""),
    }
