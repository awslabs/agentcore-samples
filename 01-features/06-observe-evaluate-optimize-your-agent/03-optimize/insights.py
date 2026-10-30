"""
Run AgentCore Insights on the HR Assistant agent.

Insights are produced by the Builtin.FailureAnalysis insight evaluator, which runs in a
standard batch evaluation alongside other evaluators:
  - Builtin.FailureAnalysis  -- detects failures in each session and groups them into
                                insights with root causes, suggested fixes, and span-level findings
  - Builtin.GoalSuccessRate  -- scores whether each session achieved the user's goal

With --generate-traces, the script first sends a set of failure-mode sessions to populate
CloudWatch, so FailureAnalysis has real patterns to work with:
  - Unknown employee IDs that the agent cannot look up
  - HR policy topics not in the system (sabbatical, floating holiday, relocation)
  - Unknown benefit types (gym membership, commuter benefits, wellness)
  - Pay stubs for unavailable periods or unknown employees
  - Ambiguous prompts that cause multi-step confusion

Usage:
    # Generate failure traces then run insights:
    python insights.py --name HRInsights849 --generate-traces [--region us-west-2]

    # Run insights on existing traces from the last N days:
    python insights.py --name HRInsights849 [--lookback-days 7]

Prerequisites:
    1. Deploy the HR Assistant agent:
         python deploy.py --name HRInsights849 --region us-west-2

    2. Install dependencies:
         pip install -r requirements.txt
"""

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

# ── Parse arguments ───────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="AgentCore Insights (Builtin.FailureAnalysis)")
parser.add_argument("--name", required=True, help="Runtime name (matches agent_state_{name}.json)")
parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
parser.add_argument(
    "--generate-traces",
    action="store_true",
    help="Send failure-mode invocations to generate diverse traces before running insights",
)
parser.add_argument(
    "--lookback-days",
    type=int,
    default=7,
    help="Number of days of traces to analyze (default: 7)",
)
args = parser.parse_args()

REGION = args.region
LOOKBACK_DAYS = args.lookback_days

# ── Load agent state ───────────────────────────────────────────────────────

STATE_FILE = Path(f"agent_state_{args.name}.json")
if not STATE_FILE.exists():
    raise FileNotFoundError(
        f"{STATE_FILE} not found. Run 'python deploy.py --name {args.name} --region {REGION}' first."
    )

state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
AGENT_ARN = state["runtime_arn"]
LOG_GROUP = state["log_group"]
SERVICE_NAME = state["service_name"]
ACCOUNT_ID = state["account_id"]
SPANS_LOG_GROUP = "aws/spans"

# Both log groups are required for reliable session coverage.
# aws/spans holds OTel span documents (used by all evaluators).
# The runtime log group holds the log events that spans reference — without it,
# the evaluator engine sees incomplete spans (LogEventMissingException) even
# though the agent executed successfully.
LOG_GROUP_NAMES = [SPANS_LOG_GROUP, LOG_GROUP]

# ── AWS clients ───────────────────────────────────────────────────────────

dp = boto3.client("bedrock-agentcore", region_name=REGION)

print(f"Account     : {ACCOUNT_ID}")
print(f"Region      : {REGION}")
print(f"Runtime     : {args.name}")
print(f"Agent ARN   : {AGENT_ARN}")
print(f"Service Name: {SERVICE_NAME}")
print(f"Log Group   : {LOG_GROUP}")

# ── Evaluator selection ───────────────────────────────────────────────────
#
# Builtin.FailureAnalysis produces the insights. Builtin.GoalSuccessRate runs in
# the same job so each session gets both a goal-success score and a failure verdict.

EVALUATORS = [
    {"evaluatorId": "Builtin.FailureAnalysis"},
    {"evaluatorId": "Builtin.GoalSuccessRate"},
]
print(f"\nEvaluators: {[e['evaluatorId'] for e in EVALUATORS]}")

# ── Step 1: Generate failure-mode traces (optional) ───────────────────────
#
# These prompts exercise different failure paths in the HR Assistant so that
# FailureAnalysis has real patterns to group. Each category below maps to
# one or more tool errors or agent reasoning failures.
#
#   Category: Tool Execution Failures
#     - get_pto_balance(employee_id="EMP-999")   -> "Employee EMP-999 not found"
#     - get_pay_stub(employee_id="EMP-003", ...) -> "Pay stub not found"
#
#   Category: Invalid Input / Bad Formatting
#     - PTO request with non-date strings -> agent loops or declines
#
#   Category: Out-of-Scope Requests
#     - Policy topics not in the system: sabbatical, floating_holiday, relocation
#     - Benefit types not in the system: gym, commuter, wellness
#
#   Category: Ambiguous Requests
#     - Vague prompts where the agent guesses incorrectly or asks for clarification

FAILURE_PROMPTS = [
    # --- Tool failure: unknown employee IDs ---
    ("EMP-999", "What is my current PTO balance?"),
    ("EMP-999", "Please submit a PTO request for me from 2026-07-01 to 2026-07-05."),
    ("EMP-003", "Can you pull up my January 2026 pay stub?"),
    ("EMP-003", "How many PTO days do I have remaining?"),
    # --- Tool failure: unavailable pay periods ---
    ("EMP-001", "Get my pay stub for December 2019."),
    ("EMP-001", "Show me my pay stub for March 2020."),
    ("EMP-042", "I need my pay stub for period 2022-06."),
    # --- Policy topics not in the system ---
    ("EMP-001", "What is the sabbatical leave policy?"),
    ("EMP-002", "Do we have a floating holiday policy?"),
    ("EMP-042", "What is the relocation assistance policy?"),
    ("EMP-001", "Explain the bereavement leave policy."),
    # --- Benefit types not in the system ---
    ("EMP-001", "What gym membership benefits does the company offer?"),
    ("EMP-002", "Tell me about our commuter benefits — transit and parking."),
    ("EMP-042", "What wellness program benefits are available?"),
    ("EMP-001", "Do we have a childcare or dependent care FSA benefit?"),
    # --- Invalid input formats ---
    ("EMP-001", "Submit PTO for me starting yesterday through the end of next month."),
    ("EMP-002", "Request time off from ASAP to whenever — for burnout recovery."),
    # --- Multi-step failures: agent finds no data, user pushes further ---
    ("EMP-999", "Check my PTO balance first, then submit a request for next week."),
    ("EMP-001", "Can you give me the 2018 annual pay summary? I need it for a loan."),
    # --- Successful sessions as a baseline for GoalSuccessRate vs. FailureAnalysis ---
    ("EMP-001", "What is my current PTO balance?"),
    ("EMP-042", "Tell me about the 401k plan — how much does the company match?"),
    ("EMP-001", "What are my health insurance options?"),
    ("EMP-002", "What is the parental leave policy for primary caregivers?"),
    ("EMP-001", "What is the remote work policy?"),
    ("EMP-042", "Can you pull up my January 2026 pay stub?"),
    ("EMP-001", "Submit a PTO request from 2026-08-11 to 2026-08-15 for a vacation."),
    ("EMP-002", "How does the dental insurance work for major procedures?"),
]

if args.generate_traces:
    print("\n" + "=" * 60)
    print("STEP 1: Generate Failure-Mode Traces")
    print("=" * 60)
    print(f"Sending {len(FAILURE_PROMPTS)} sessions (failure + success mix)...\n")

    session_ids = []
    success_count = 0
    error_count = 0

    for i, (emp_id, prompt) in enumerate(FAILURE_PROMPTS):
        session_id = str(uuid.uuid4())
        session_ids.append(session_id)
        full_prompt = f"Employee ID: {emp_id}. {prompt}" if emp_id != "custom" else prompt

        try:
            resp = dp.invoke_agent_runtime(
                agentRuntimeArn=AGENT_ARN,
                runtimeSessionId=session_id,
                payload=json.dumps({"prompt": full_prompt}).encode(),
            )
            resp["response"].read()
            success_count += 1
            status_tag = "OK "  # pylint: disable=invalid-name
        except Exception as e:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            error_count += 1
            status_tag = "ERR"  # pylint: disable=invalid-name

        print(f"  [{i + 1:2d}] {status_tag} {session_id[:8]}... [{emp_id}] {prompt[:60]}")

    print(f"\nSent {success_count} OK, {error_count} errors (invoke errors; tool errors are expected)")
    print("Waiting 3 minutes for traces to propagate to CloudWatch...")

    for remaining in range(180, 0, -30):
        print(f"  {remaining}s remaining...")
        time.sleep(30)

    print("CloudWatch ingestion complete.")
else:
    print("\n(Skipping trace generation — use --generate-traces to send failure-mode sessions first)")

# ── Step 2: Run Batch Insights ─────────────────────────────────────────────
#
# Notes on dataSourceConfig for insights:
#   - "aws/spans" holds OTel span documents and is required for session coverage
#   - The runtime log group (/aws/bedrock-agentcore/runtimes/...) must also be
#     included; without it the engine cannot resolve log events referenced by spans

print("\n" + "=" * 60)
print("STEP 2: Start Batch Insights")
print("=" * 60)

now = datetime.now(timezone.utc)  # noqa: UP017 - datetime.UTC requires Python 3.11; this sample supports 3.10+
start_time = now - timedelta(days=LOOKBACK_DAYS)

EVAL_NAME = f"HRInsights{uuid.uuid4().hex[:8]}"

print(f"Batch eval name : {EVAL_NAME}")
print(f"Time range      : {start_time.strftime('%Y-%m-%dT%H:%M:%SZ')} to {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
print(f"Service name    : {SERVICE_NAME}")
print(f"Log groups      : {LOG_GROUP_NAMES}")

eval_resp = dp.start_batch_evaluation(
    batchEvaluationName=EVAL_NAME,
    evaluators=EVALUATORS,
    dataSourceConfig={
        "cloudWatchLogs": {
            "serviceNames": [SERVICE_NAME],
            "logGroupNames": LOG_GROUP_NAMES,
            "filterConfig": {
                "timeRange": {
                    "startTime": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            },
        }
    },
    clientToken=str(uuid.uuid4()),
)

EVAL_ID = eval_resp["batchEvaluationId"]
EVAL_ARN = eval_resp.get("batchEvaluationArn", "")
print(f"\nStarted  : {EVAL_ID}")
print(f"ARN      : {EVAL_ARN}")

# ── Step 3: Poll for completion ────────────────────────────────────────────

print("\n" + "=" * 60)
print("STEP 3: Poll for Completion")
print("=" * 60)

TERMINAL = {"COMPLETED", "FAILED", "STOPPED", "COMPLETED_WITH_ERRORS"}
poll = 0
result = {}

while True:
    poll += 1
    result = dp.get_batch_evaluation(batchEvaluationId=EVAL_ID)
    status = result["status"]
    er = result.get("evaluationResults", {})
    completed = er.get("numberOfSessionsCompleted", "?")
    total = er.get("totalNumberOfSessions", "?")
    failed = er.get("numberOfSessionsFailed", "?")
    print(
        f"  Poll {poll:3d}  [{time.strftime('%H:%M:%S')}]  status={status}  "
        f"completed={completed}/{total}  failed={failed}"
    )

    if status in TERMINAL:
        break

    time.sleep(30)

print(f"\nFinal status: {status}")

# ── Step 4: Print Insights Results ────────────────────────────────────────

print("\n" + "=" * 60)
print("STEP 4: Insights Results")
print("=" * 60)

# ── 4a: Evaluator scores ──────────────────────────────────────────────────
#
# FailureAnalysis scores each session 1.0 (failures found) or 0.0 (no failures),
# so a higher average means more failing sessions.

er = result.get("evaluationResults", {})
summaries = er.get("evaluatorSummaries", [])
print(f"\n--- Evaluator scores ({len(summaries)} evaluators) ---")
for s in summaries:
    avg = s.get("statistics", {}).get("averageScore")
    print(f"  {s['evaluatorId']:<26}: {avg}  (evaluated: {s.get('totalEvaluated', 0)})")

# ── 4b: FailureAnalysis insights ──────────────────────────────────────────

insights = result.get("insightResults", [])
print(f"\n--- FailureAnalysis ({len(insights)} insights) ---")

if not insights:
    print("  No insights found.")
else:
    for insight in insights:
        print(f"\n  Insight: {insight.get('name', '(unnamed)')}  ({insight.get('affectedSessionCount', 0)} sessions)")
        if insight.get("description"):
            print(f"  Description  : {insight['description']}")

        for cause in insight.get("rootCauses", []):
            print(
                f"\n    Root cause   : {cause.get('name', '(unnamed)')}  "
                f"({cause.get('affectedSessionCount', 0)} sessions)"
            )
            if cause.get("explanation"):
                print(f"    Explanation  : {cause['explanation']}")
            if cause.get("suggestedFix"):
                print(f"    Suggested fix: {cause['suggestedFix']}")

            for sample in cause.get("sampleSessions", []):
                for finding in sample.get("findings", []):
                    print(
                        f"      [{sample['sessionId'][:8]}] span {finding.get('targetId')}  "
                        f"{finding.get('category')} ({finding.get('confidence')}): {finding.get('description')}"
                    )

# ── 4c: Error details ─────────────────────────────────────────────────────
#
# The job ends in COMPLETED_WITH_ERRORS when some sessions fail to evaluate or when
# insight generation fails. In the second case insightResults is absent. Per-session
# scores are still available in both cases.

error_details = result.get("errorDetails", [])
if error_details:
    print("\n--- Error details ---")
    for item in error_details:
        print(f"  {item}")

# ── Summary ────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print("INSIGHTS SUMMARY")
print("=" * 60)
print(f"Batch evaluation ID : {EVAL_ID}")
print(f"Status              : {status}")
print(f"Sessions completed  : {er.get('numberOfSessionsCompleted', 'N/A')}/{er.get('totalNumberOfSessions', 'N/A')}")
print(f"Sessions failed     : {er.get('numberOfSessionsFailed', 'N/A')}")

print(f"Insights found      : {len(insights)}")

print("\nAverage scores (FailureAnalysis: higher = more failing sessions):")
for s in summaries:
    print(f"  {s['evaluatorId']:<26}: {s.get('statistics', {}).get('averageScore')}")

print("\nFull response saved to insights_result.json")
with open("insights_result.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, default=str)
