"""
AgentCore Optimization — System Prompt Recommendation for the Weather Agent.

Analyzes traces from your weather agent sessions and generates an AI-improved
system prompt optimized for a target evaluator (e.g., Helpfulness, GoalSuccessRate).

Prerequisites:
    - The weather agent web app must have been running with some chat sessions
      (traces need to exist in CloudWatch)
    - AWS_DEFAULT_REGION set
    - Transaction Search enabled in CloudWatch

Usage:
    # Run after using the web app for a few sessions:
    python optimize.py

    # Specify evaluator to optimize for:
    python optimize.py --evaluator Builtin.Helpfulness

    # Use a custom time range (last N days):
    python optimize.py --lookback 1

    # Cleanup recommendations:
    python optimize.py --cleanup
"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.client import get_agentcore_client

# -- CLI -----------------------------------------------------------------------
parser = argparse.ArgumentParser(
    description="Generate an optimized system prompt for the Weather Agent"
)
parser.add_argument(
    "--evaluator",
    default="Builtin.GoalSuccessRate",
    help="Evaluator to optimize for (default: Builtin.GoalSuccessRate)",
)
parser.add_argument(
    "--lookback",
    type=int,
    default=7,
    help="Days of traces to analyze (default: 7)",
)
parser.add_argument(
    "--cleanup",
    action="store_true",
    help="Delete all weather_rec_* recommendations and exit",
)
args = parser.parse_args()

# -- Configuration -------------------------------------------------------------
REGION = boto3.session.Session().region_name or "us-east-1"
STATE_FILE = Path(__file__).parent / "resource_info.json"

CURRENT_SYSTEM_PROMPT = (
    "You are a weather assistant. You ONLY answer questions about weather, "
    "climate, and atmospheric conditions (temperature, wind, humidity, UV index, "
    "sunrise, sunset, moon phase, forecasts, air quality, precipitation). "
    "If the user asks about anything unrelated to weather, politely redirect them. "
    "For example: 'I'm a weather assistant — I can help with forecasts, current conditions, "
    "UV index, wind, sunrise/sunset, and more. What location would you like weather for?' "
    "When answering weather questions: always search for real-time data using your tools, "
    "include specific numbers with units (temperature in F/C, wind in km/h or mph), "
    "mention the city name in your response, and keep responses concise and well-structured."
)

# -- Clients -------------------------------------------------------------------
dp_client = boto3.client("bedrock-agentcore", region_name=REGION)
logs_client = boto3.client("logs", region_name=REGION)


# -- Helpers -------------------------------------------------------------------
def discover_log_group(harness_name: str) -> tuple[str, str] | None:
    """Find the log group ARN and service name for a harness."""
    prefix = f"/aws/bedrock-agentcore/runtimes/harness_{harness_name}-"
    resp = logs_client.describe_log_groups(logGroupNamePrefix=prefix, limit=5)
    groups = resp.get("logGroups", [])
    if not groups:
        return None
    groups.sort(key=lambda g: g.get("creationTime", 0), reverse=True)
    log_group = groups[0]
    log_group_name = log_group["logGroupName"]
    log_group_arn = log_group["arn"]

    # Service name: harness_{name}.DEFAULT (without random suffix)
    basename = log_group_name.split("/")[-1]
    parts = basename.rsplit("-", 2)
    service_name = f"{parts[0]}.DEFAULT" if len(parts) >= 3 else basename.replace("-DEFAULT", ".DEFAULT")

    return log_group_arn, service_name


def cleanup_recommendations():
    """Delete all weather_rec_* recommendations."""
    try:
        resp = dp_client.list_recommendations()
        recs = resp.get("recommendationSummaries", resp.get("recommendations", resp.get("items", [])))
        count = 0
        for rec in recs:
            name = rec.get("name", "")
            rec_id = rec.get("recommendationId", "")
            if name.startswith("weather_rec_"):
                try:
                    dp_client.delete_recommendation(recommendationId=rec_id)
                    print(f"  Deleted: {name}")
                    count += 1
                except Exception as e:
                    print(f"  Warning: {e}")
        if count == 0:
            print("  No weather_rec_* recommendations found")
    except Exception as e:
        print(f"  Error: {e}")


# -- Main ----------------------------------------------------------------------
def main():
    if args.cleanup:
        print("Cleaning up recommendations...")
        cleanup_recommendations()
        return

    print("=" * 65)
    print("AgentCore Optimization — System Prompt Recommendation")
    print("=" * 65)

    # Load state
    if not STATE_FILE.exists():
        print("\nError: resource_info.json not found.")
        print("Run ./start.sh first to create the weather agent, then use it for a few sessions.")
        sys.exit(1)

    state = json.loads(STATE_FILE.read_text())
    harness_name = state.get("harness_name")
    if not harness_name:
        print("\nError: harness_name not found in resource_info.json")
        sys.exit(1)

    print(f"\n  Harness:    {harness_name}")
    print(f"  Region:     {REGION}")
    print(f"  Evaluator:  {args.evaluator}")
    print(f"  Lookback:   {args.lookback} day(s)")

    # Discover log group
    print("\n  Discovering log group...")
    result = discover_log_group(harness_name)
    if not result:
        print("  Error: Could not find log group for this harness.")
        print("  Make sure the web app has been running and you've sent some messages.")
        sys.exit(1)

    log_group_arn, service_name = result
    print(f"  Log group:  {log_group_arn.split(':log-group:')[-1].rstrip(':*')}")
    print(f"  Service:    {service_name}")

    # Time range
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(days=args.lookback)

    # Start recommendation
    rec_name = f"weather_rec_{uuid.uuid4().hex[:8]}"
    print(f"\n  Starting recommendation: {rec_name}")
    print(f"  Analyzing traces from {start_time.strftime('%Y-%m-%d %H:%M')} to {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Optimizing for: {args.evaluator}")

    try:
        resp = dp_client.start_recommendation(
            name=rec_name,
            type="SYSTEM_PROMPT_RECOMMENDATION",
            recommendationConfig={
                "systemPromptRecommendationConfig": {
                    "systemPrompt": {
                        "text": CURRENT_SYSTEM_PROMPT,
                    },
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
                            {"evaluatorArn": f"arn:aws:bedrock-agentcore:::evaluator/{args.evaluator}"}
                        ]
                    },
                }
            },
            clientToken=str(uuid.uuid4()),
        )
    except Exception as e:
        print(f"\n  Error starting recommendation: {e}")
        sys.exit(1)

    rec_id = resp["recommendationId"]
    print(f"  Recommendation ID: {rec_id}")
    print(f"  Status: {resp.get('status', 'PENDING')}")

    # Poll for completion
    print("\n  Waiting for recommendation to complete (typically 2-5 minutes)...")
    status = "PENDING"
    for i in range(60):
        time.sleep(10)
        try:
            result = dp_client.get_recommendation(recommendationId=rec_id)
            status = result.get("status", "UNKNOWN")
            if i % 3 == 0:
                print(f"    [{i * 10}s] {status}")
            if status in ("COMPLETED", "FAILED"):
                break
        except Exception as e:
            print(f"    Error polling: {e}")

    if status != "COMPLETED":
        print(f"\n  Recommendation did not complete (status: {status})")
        if status == "FAILED":
            error_msg = result.get("recommendationResult", {}).get(
                "systemPromptRecommendationResult", {}
            ).get("errorMessage", "Unknown error")
            print(f"  Error: {error_msg}")
        sys.exit(1)

    # Extract result
    rec_result = result.get("recommendationResult", {}).get(
        "systemPromptRecommendationResult", {}
    )
    recommended_prompt = rec_result.get("recommendedSystemPrompt", "")
    explanation = rec_result.get("explanation", "")

    # Display results
    print("\n" + "=" * 65)
    print("RECOMMENDATION RESULT")
    print("=" * 65)

    print("\n--- Current System Prompt ---")
    print(CURRENT_SYSTEM_PROMPT[:300])
    if len(CURRENT_SYSTEM_PROMPT) > 300:
        print(f"  ... ({len(CURRENT_SYSTEM_PROMPT)} chars total)")

    print("\n--- Recommended System Prompt ---")
    print(recommended_prompt[:500])
    if len(recommended_prompt) > 500:
        print(f"  ... ({len(recommended_prompt)} chars total)")

    print("\n--- Explanation ---")
    print(explanation[:500])

    # Save result
    output_file = Path(__file__).parent / "optimization_result.json"
    output_data = {
        "recommendation_id": rec_id,
        "recommendation_name": rec_name,
        "evaluator": args.evaluator,
        "current_system_prompt": CURRENT_SYSTEM_PROMPT,
        "recommended_system_prompt": recommended_prompt,
        "explanation": explanation,
        "timestamp": now.isoformat(),
    }
    output_file.write_text(json.dumps(output_data, indent=2))
    print(f"\n  Full result saved to: {output_file.name}")

    print("\n" + "=" * 65)
    print("Next steps:")
    print("  1. Review the recommended prompt above")
    print("  2. Update backend/agent.py SYSTEM_PROMPT with the recommendation")
    print("  3. Restart the app and compare agent behavior")
    print("  4. Run a batch evaluation to measure improvement")
    print(f"\n  View in console: Bedrock AgentCore > Optimizations > Recommendations")
    print("=" * 65)


if __name__ == "__main__":
    main()
