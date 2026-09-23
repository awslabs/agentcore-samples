"""
Run AgentCore Insights on the HR Assistant agent.

Three insight types:
  - Builtin.Insight.FailureAnalysis   -- clusters failure sessions by root cause category
  - Builtin.Insight.UserIntent        -- groups sessions by what the user was trying to do
  - Builtin.Insight.ExecutionSummary  -- summarizes agent execution patterns across sessions

With --generate-traces, the script first sends a set of failure-mode sessions to populate
CloudWatch, so FailureAnalysis has real patterns to work with:
  - Unknown employee IDs that the agent cannot look up
  - HR policy topics not in the system (sabbatical, floating holiday, relocation)
  - Unknown benefit types (gym membership, commuter benefits, wellness)
  - Pay stubs for unavailable periods or unknown employees
  - Ambiguous prompts that cause multi-step confusion

Usage:
    # Generate failure traces then run all three insights:
    python insights.py --name HRInsights849 --generate-traces [--region us-west-2]

    # Run insights on existing traces from the last N days:
    python insights.py --name HRInsights849 [--lookback-days 7]

    # Also create an OnlineEvaluationConfig for recurring daily insights:
    python insights.py --name HRInsights849 --generate-traces --online

    # Run FailureAnalysis only (skips UserIntent and ExecutionSummary):
    python insights.py --name HRInsights849 --insight Builtin.Insight.FailureAnalysis

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

parser = argparse.ArgumentParser(description="AgentCore Insights (FailureAnalysis, UserIntent, ExecutionSummary)")
parser.add_argument("--name", required=True, help="Runtime name (matches agent_state_{name}.json)")
parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
parser.add_argument(
    "--generate-traces",
    action="store_true",
    help="Send failure-mode invocations to generate diverse traces before running insights",
)
parser.add_argument(
    "--online",
    action="store_true",
    help="Also create an OnlineEvaluationConfig for recurring daily insights",
)
parser.add_argument(
    "--lookback-days",
    type=int,
    default=7,
    help="Number of days of traces to analyze (default: 7)",
)
parser.add_argument(
    "--insight",
    action="append",
    default=None,
    dest="insights",
    metavar="INSIGHT_ID",
    help=(
        "Specific insight IDs to run. May be repeated. "
        "Default: all three (FailureAnalysis, UserIntent, ExecutionSummary). "
        "Example: --insight Builtin.Insight.FailureAnalysis"
    ),
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
ROLE_ARN = state["role_arn"]
ACCOUNT_ID = state["account_id"]
SPANS_LOG_GROUP = "aws/spans"

# Both log groups are required for reliable session coverage.
# aws/spans holds OTel span documents (used by all insight types).
# The runtime log group holds the log events that spans reference — without it,
# the evaluator engine sees incomplete spans (LogEventMissingException) even
# though the agent executed successfully.
LOG_GROUP_NAMES = [SPANS_LOG_GROUP, LOG_GROUP]

# ── AWS clients ───────────────────────────────────────────────────────────

dp = boto3.client("bedrock-agentcore", region_name=REGION)
ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

print(f"Account     : {ACCOUNT_ID}")
print(f"Region      : {REGION}")
print(f"Runtime     : {args.name}")
print(f"Agent ARN   : {AGENT_ARN}")
print(f"Service Name: {SERVICE_NAME}")
print(f"Log Group   : {LOG_GROUP}")

# ── Insight selection ─────────────────────────────────────────────────────

ALL_INSIGHTS = [
    "Builtin.Insight.FailureAnalysis",
    "Builtin.Insight.UserIntent",
    "Builtin.Insight.ExecutionSummary",
]

SELECTED_INSIGHTS = args.insights if args.insights else ALL_INSIGHTS
print(f"\nInsights to run: {SELECTED_INSIGHTS}")

# ── Step 1: Generate failure-mode traces (optional) ───────────────────────
#
# These prompts exercise different failure paths in the HR Assistant so that
# FailureAnalysis has real patterns to cluster. Each category below maps to
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
    # --- Successful sessions for user intent diversity ---
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
        except Exception as e:  # pylint: disable=broad-exception-caught
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
#   - insights and evaluators are mutually exclusive -- do not mix them

print("\n" + "=" * 60)
print("STEP 2: Start Batch Insights")
print("=" * 60)

now = datetime.now(timezone.utc)
start_time = now - timedelta(days=LOOKBACK_DAYS)

EVAL_NAME = f"HRInsights{uuid.uuid4().hex[:8]}"

print(f"Batch eval name : {EVAL_NAME}")
print(f"Time range      : {start_time.strftime('%Y-%m-%dT%H:%M:%SZ')} to {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
print(f"Service name    : {SERVICE_NAME}")
print(f"Log groups      : {LOG_GROUP_NAMES}")

insights_list = [{"insightId": iid} for iid in SELECTED_INSIGHTS]

eval_resp = dp.start_batch_evaluation(
    batchEvaluationName=EVAL_NAME,
    insights=insights_list,
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
    processed = result.get("statistics", {}).get("processedSessionCount", "?")
    failed = result.get("statistics", {}).get("failedSessionCount", "?")
    print(f"  Poll {poll:3d}  [{time.strftime('%H:%M:%S')}]  status={status}  processed={processed}  failed={failed}")

    if status in TERMINAL:
        break

    time.sleep(30)

print(f"\nFinal status: {status}")

if result.get("errorDetails"):
    print(f"Error details: {result['errorDetails']}")

# ── Step 4: Print Insights Results ────────────────────────────────────────

print("\n" + "=" * 60)
print("STEP 4: Insights Results")
print("=" * 60)

# ── 4a: FailureAnalysis ───────────────────────────────────────────────────

fa = result.get("failureAnalysisResult") or result.get("failureAnalysisOutput")
if fa:
    failures = fa.get("failures", [])
    print(f"\n--- FailureAnalysis ({len(failures)} top-level categories) ---")

    if not failures:
        print("  No failure categories found.")
    else:
        for cat in failures:
            cat_name = cat.get("name", "(unnamed)")
            sub_cats = cat.get("subCategories", [])
            total_affected = sum(
                rc.get("affectedSessionCount", 0) for sc in sub_cats for rc in sc.get("rootCauses", [])
            )
            print(f"\n  Category: {cat_name}  (sessions affected: {total_affected})")

            for sc in sub_cats:
                sc_name = sc.get("name", "(unnamed)")
                root_causes = sc.get("rootCauses", [])
                print(f"    Subcategory: {sc_name}")

                for rc in root_causes:
                    rc_name = rc.get("name", "(unnamed)")
                    rc_rec = rc.get("recommendation", "")
                    rc_count = rc.get("affectedSessionCount", 0)
                    rc_sessions = rc.get("affectedSessions", [])
                    print(f"      Root cause   : {rc_name}  ({rc_count} sessions)")
                    if rc_rec:
                        print(f"      Recommendation: {rc_rec}")
                    if rc_sessions:
                        preview = rc_sessions[:3]
                        more = len(rc_sessions) - 3
                        suffix = f" (+{more} more)" if more > 0 else ""
                        print(f"      Session IDs  : {preview}{suffix}")
else:
    print("\n(No failureAnalysisResult in response)")

# ── 4b: UserIntent ────────────────────────────────────────────────────────

ui = result.get("userIntentResult") or result.get("userIntentOutput")
if ui:
    intents = ui.get("userIntents", [])
    print(f"\n--- UserIntent ({len(intents)} intent clusters) ---")

    if not intents:
        print("  No user intent clusters found.")
    else:
        for intent in intents:
            cluster_id = intent.get("clusterId", "")
            name = intent.get("name", "(unnamed)")
            description = intent.get("description", "")
            count = intent.get("affectedSessionCount", 0)
            print(f"\n  Intent cluster: {name}  ({count} sessions)")
            print(f"  Cluster ID    : {cluster_id}")
            if description:
                print(f"  Description   : {description}")
else:
    print("\n(No userIntentResult in response — may be a known beta issue)")

# ── 4c: ExecutionSummary ──────────────────────────────────────────────────

es = result.get("executionSummaryResult") or result.get("executionSummaryOutput")
if es:
    summaries = es.get("executionSummaries", [])
    print(f"\n--- ExecutionSummary ({len(summaries)} clusters) ---")

    if not summaries:
        print("  No execution summary clusters found.")
        print("  Note: ExecutionSummary requires at least 3 sessions for clustering.")
    else:
        for summary in summaries:
            cluster_id = summary.get("clusterId", "")
            description = summary.get("description", "")
            count = summary.get("affectedSessionCount", 0)
            print(f"\n  Cluster: {cluster_id}  ({count} sessions)")
            if description:
                print(f"  Description: {description}")
else:
    print("\n(No executionSummaryResult in response)")

# ── 4d: Error details per insight ─────────────────────────────────────────

error_details = result.get("errorDetails", [])
if error_details:
    print("\n--- Error details ---")
    if isinstance(error_details, dict):
        for key, val in error_details.items():
            print(f"  {key}: {val}")
    else:
        for item in error_details:
            print(f"  {item}")

# ── Step 5: Online Insights Config (optional) ──────────────────────────────
#
# Creates a recurring insights job that runs daily over the last 24 hours of
# traces. Results accumulate automatically with no manual intervention needed.

if args.online:
    print("\n" + "=" * 60)
    print("STEP 5: Create Online Insights Config (Daily Recurring)")
    print("=" * 60)

    ONLINE_NAME = f"HROnlineInsights{uuid.uuid4().hex[:6]}"

    online_resp = ctrl.create_online_evaluation_config(
        onlineEvaluationConfigName=ONLINE_NAME,
        description="HR Assistant daily insights: FailureAnalysis, UserIntent, ExecutionSummary",
        rule={
            "samplingConfig": {"samplingPercentage": 100},
        },
        dataSourceConfig={
            "cloudWatchLogs": {
                "logGroupNames": LOG_GROUP_NAMES,
                "serviceNames": [SERVICE_NAME],
            }
        },
        insights=[{"insightId": iid} for iid in SELECTED_INSIGHTS],
        clusteringConfig={"frequencies": ["DAILY"]},
        evaluationExecutionRoleArn=ROLE_ARN,
        enableOnCreate=True,
        clientToken=str(uuid.uuid4()),
    )

    ONLINE_ID = online_resp["onlineEvaluationConfigId"]
    ONLINE_ARN = online_resp["onlineEvaluationConfigArn"]

    print("Online insights config created:")
    print(f"  ID    : {ONLINE_ID}")
    print(f"  ARN   : {ONLINE_ARN}")
    print(f"  Name  : {ONLINE_NAME}")
    print(f"  Status: {online_resp.get('executionStatus', 'unknown')}")
    print()
    print("The config will run daily. To view results:")
    print(
        f"  python -c \"import boto3, json; ctrl=boto3.client('bedrock-agentcore-control', "
        f"region_name='{REGION}'); r=ctrl.get_online_evaluation_config("
        f"onlineEvaluationConfigId='{ONLINE_ID}'); print(json.dumps(r, indent=2, default=str))\""
    )
    print()
    print("To archive (disable) this config:")
    print(f"  ctrl.update_online_evaluation_config(onlineEvaluationConfigId='{ONLINE_ID}', executionStatus='DISABLED')")

# ── Summary ────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print("INSIGHTS SUMMARY")
print("=" * 60)
print(f"Batch evaluation ID : {EVAL_ID}")
print(f"Status              : {status}")

stats = result.get("statistics", {})
if stats:
    print(f"Sessions processed  : {stats.get('processedSessionCount', 'N/A')}")
    print(f"Sessions failed     : {stats.get('failedSessionCount', 'N/A')}")

fa_clusters = len((result.get("failureAnalysisResult") or {}).get("failures", []))
ui_clusters = len((result.get("userIntentResult") or {}).get("userIntents", []))
es_clusters = len((result.get("executionSummaryResult") or {}).get("executionSummaries", []))

print(f"\nFailureAnalysis  : {fa_clusters} top-level categories")
print(f"UserIntent       : {ui_clusters} intent clusters")
print(f"ExecutionSummary : {es_clusters} execution clusters")

print("\nFull response saved to insights_result.json")
with open("insights_result.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, default=str)
