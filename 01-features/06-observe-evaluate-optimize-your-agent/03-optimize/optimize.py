"""
AgentCore Optimization end-to-end workflow for the HR Assistant agent.

Demonstrates the full optimization loop:
  1. Deploy v1 HR Assistant (or load existing deployment)
  2. Create baseline Configuration Bundle and send traffic
  3. Run baseline batch evaluation (GoalSuccessRate, Helpfulness, Correctness)
  4. Generate system prompt recommendation from production traces
  5. Generate tool description recommendation from production traces
  6. Create control and treatment configuration bundles
  7. A/B test via config-bundle routing (same runtime, different configs)
  8. Deploy v2 and run target-based A/B test (phased canary rollout)
  9. Cleanup all AWS resources

Usage:
    # Full workflow (deploys agents automatically):
    python optimize.py --name HRAssistV1 [--region us-east-1]

    # Skip deployment (use existing state files):
    python optimize.py --name HRAssistV1 --skip-deploy

    # Cleanup only:
    python optimize.py --name HRAssistV1 --cleanup-only

Prerequisites:
    - pip installed (for building ARM64 deployment packages)
    - AWS CLI configured with credentials
    - IAM permissions: bedrock-agentcore:*, bedrock:InvokeModel, iam:*, s3:*, logs:*, xray:*
"""

import argparse
import atexit
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import requests as http_requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# ── Parse arguments ───────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="AgentCore Optimization workflow")
parser.add_argument("--name", required=True, help="Base runtime name (alphanumeric)")
parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
parser.add_argument(
    "--skip-deploy",
    action="store_true",
    help="Skip deployment; load state from existing agent_state_*.json files",
)
parser.add_argument(
    "--cleanup-only",
    action="store_true",
    help="Skip all demo steps and only run cleanup",
)
args = parser.parse_args()

REGION = args.region
SUFFIX = uuid.uuid4().hex[:6]
V1_NAME = args.name
V2_NAME = f"{args.name}v2"

# ── AWS clients ───────────────────────────────────────────────────────────

sts = boto3.client("sts", region_name=REGION)
ACCOUNT_ID = sts.get_caller_identity()["Account"]

ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
dp = boto3.client("bedrock-agentcore", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
xray = boto3.client("xray", region_name=REGION)

print(f"ACCOUNT_ID = {ACCOUNT_ID}")
print(f"REGION     = {REGION}")
print(f"SUFFIX     = {SUFFIX}")
print(f"V1_NAME    = {V1_NAME}")
print(f"V2_NAME    = {V2_NAME}")

SCRIPT_DIR = Path(__file__).parent

# Online evaluation configs consider a session closed after this many minutes
# without a new request. No session can be scored, and so no A/B result can
# include it, before its timeout elapses.
SESSION_TIMEOUT_MINUTES = 2

# ── State tracking (populated as resources are created) ───────────────────

AGENT_ARN = None
LOG_GROUP = None
SERVICE_NAME = None
ROLE_ARN = None
ROLE_NAME = None
S3_BUCKET = None
SPANS_LOG_GROUP = "aws/spans"
AGENT_ARN_V2 = None
LOG_GROUP_V2 = None
SERVICE_NAME_V2 = None
ROLE_ARN_V2 = None
BASELINE_BUNDLE_ID = None
BASELINE_BUNDLE_ARN = None
BASELINE_BUNDLE_VERSION = None
CONTROL_BUNDLE_ID = None
CONTROL_BUNDLE_ARN = None
CONTROL_BUNDLE_VERSION = None
TREATMENT_BUNDLE_ID = None
TREATMENT_BUNDLE_ARN = None
TREATMENT_BUNDLE_VERSION = None
GATEWAY_ID = None
GATEWAY_ARN = None
GATEWAY_URL = None
TARGET_ID = None
TARGET_NAME = "HRAgentV1"
TARGET_ID_V2 = None
TARGET_NAME_V2 = "HRAgentV2"
ONLINE_EVAL_ID = None
ONLINE_EVAL_ARN = None
ONLINE_EVAL_V2_ID = None
ONLINE_EVAL_V2_ARN = None
ONLINE_EVAL_TARGET_V1_ID = None
ONLINE_EVAL_TARGET_V1_ARN = None
ABTEST_BUNDLE_ID = None
ABTEST_TARGET_ID = None
DELIVERY_ID = None
DELIVERY_SOURCE_NAME = f"hr-gw-traces-{SUFFIX}"


def _save_state():
    """Save whatever state has been collected so far. Called on exit (success or failure)."""
    state_file = Path(f"optimize_state_{V1_NAME}.json")
    try:
        _g = globals()
        state = {
            "region": REGION,
            "runtime_id": _g.get("v1_state", {}).get("runtime_id"),
            "runtime_id_v2": _g.get("v2_state", {}).get("runtime_id"),
            "role_name": ROLE_NAME,
            "role_name_v2": _g.get("v2_state", {}).get("role_name"),
            "baseline_bundle_id": BASELINE_BUNDLE_ID,
            "control_bundle_id": CONTROL_BUNDLE_ID,
            "treatment_bundle_id": TREATMENT_BUNDLE_ID,
            "gateway_id": GATEWAY_ID,
            "target_id": TARGET_ID,
            "target_id_v2": TARGET_ID_V2,
            "online_eval_id": ONLINE_EVAL_ID,
            "online_eval_v2_id": ONLINE_EVAL_V2_ID,
            "online_eval_target_v1_id": ONLINE_EVAL_TARGET_V1_ID,
            "abtest_bundle_id": ABTEST_BUNDLE_ID,
            "abtest_target_id": ABTEST_TARGET_ID,
            "delivery_id": DELIVERY_ID,
            "delivery_source_name": DELIVERY_SOURCE_NAME,
        }
        state_file.write_text(json.dumps(state, indent=2))
        print(f"\nState saved to {state_file}")
    except Exception as e:
        print(f"\nWarning: could not save state to {state_file}: {e}")


atexit.register(_save_state)

CURRENT_SYSTEM_PROMPT = """You are a helpful HR Assistant for Acme Corp.

You help employees with:
- Checking PTO (paid time off) balances
- Submitting PTO requests
- Looking up HR policies (PTO, remote work, parental leave, code of conduct)
- Understanding employee benefits (health, dental, vision, 401k, life insurance)
- Retrieving pay stub information

Always use the available tools to answer questions accurately. Do not make up
policy details, benefit amounts, or pay information — look them up.
Be concise, professional, and friendly."""

CURRENT_TOOL_DESCRIPTIONS = {
    "get_pto_balance": "Return the current PTO balance for an employee.",
    "submit_pto_request": "Submit a PTO request for an employee.",
    "lookup_hr_policy": "Look up a company HR policy document by topic.",
    "get_benefits_summary": "Return a summary of a specific employee benefit.",
    "get_pay_stub": "Retrieve a pay stub for an employee for a specific pay period.",
}

RECOMMENDED_SYSTEM_PROMPT = CURRENT_SYSTEM_PROMPT
RECOMMENDED_TOOL_DESCRIPTIONS = dict(CURRENT_TOOL_DESCRIPTIONS)


# ── Helper: fetch batch eval scores from CloudWatch ───────────────────────


def fetch_eval_scores(eval_id: str) -> dict:
    """Read batch evaluation scores from the CloudWatch results log group."""
    log_group = "/aws/bedrock-agentcore/evaluations/batch-evaluations/results/default"
    stream_name = f"run-{eval_id[:8]}"
    try:
        events = logs.get_log_events(
            logGroupName=log_group,
            logStreamName=stream_name,
        ).get("events", [])
    except Exception as e:
        print(f"  CloudWatch fallback failed: {e}")
        return {}

    scores: dict[str, list] = {}
    for event in events:
        try:
            data = json.loads(event["message"])
            attrs = data.get("attributes", data)
            name = attrs.get("gen_ai.evaluation.name")
            score = attrs.get("gen_ai.evaluation.score.value")
            if name and score is not None:
                scores.setdefault(name, []).append(float(score))
        except Exception:
            continue
    return {k: sum(v) / len(v) for k, v in scores.items() if v}


# ── Helpers: A/B test results ─────────────────────────────────────────────


def _percent_change(control_stats: dict, variant: dict):
    """Use the service's percentChange, or derive it from the two means."""
    pct = variant.get("percentChange")
    if pct is None:
        cm, vm = control_stats.get("mean"), variant.get("mean")
        if cm is not None and vm is not None and float(cm) != 0:
            pct = (float(vm) - float(cm)) / float(cm) * 100
    return pct


def poll_ab_results(ab_test_id: str, not_before: datetime, control_label: str, treatment_label: str):
    """Poll an A/B test until it reports results computed after `not_before`.

    `not_before` is the time the last test session can close (last request +
    session timeout). An analysis computed earlier cannot include this run's
    sessions, so it is shown but not accepted. Returns the results dict, or
    None if no fresh analysis appears within the polling window.
    """
    print(f"Accepting only analyses computed after {not_before.strftime('%H:%M:%S')} UTC")
    print("(last request + session timeout; earlier analyses cannot include this run's sessions)")
    for poll in range(25):
        ab = dp.get_ab_test(abTestId=ab_test_id)
        results = ab.get("results") or {}
        metrics = results.get("evaluatorMetrics", [])
        analysis_ts = results.get("analysisTimestamp")
        print(f"--- Poll {poll + 1}/25 -- {time.strftime('%H:%M:%S')} ---")
        print(f"  analysisTimestamp: {analysis_ts or 'none'}")
        for m in metrics:
            name = m.get("evaluatorArn", "").split("/")[-1]
            cs = m.get("controlStats", {})
            line = f"  {name}: {control_label} mean={cs.get('mean', '-')} n={cs.get('sampleSize', '-')}"
            for vr in m.get("variantResults", []):
                pct = _percent_change(cs, vr)
                delta = f" change={pct:+.1f}%" if pct is not None else ""
                line += (
                    f" | {treatment_label} mean={vr.get('mean', '-')} n={vr.get('sampleSize', '-')}"
                    f" p={vr.get('pValue', 'N/A')}{delta}"
                )
            print(line)
        if analysis_ts and metrics:
            if analysis_ts >= not_before:
                print("Results available!")
                return results
            print("  Analysis predates this run's sessions -- waiting for a fresh one.")
        print()
        time.sleep(60)
    print("No fresh analysis within the polling window. Check the test later with get_ab_test.")
    return None


def decide_ab_outcome(results: dict) -> str:
    """Apply the README decision framework to A/B results.

    Returns "REGRESSION" if any evaluator is significantly worse for the
    treatment, "WIN" if at least one is significantly better and none is
    significantly worse, otherwise "INCONCLUSIVE". The built-in evaluators used
    here (GoalSuccessRate, Helpfulness) are higher-is-better.
    """
    any_win, any_regression = False, False
    for m in results.get("evaluatorMetrics", []):
        name = m.get("evaluatorArn", "").split("/")[-1]
        cs = m.get("controlStats", {})
        variants = m.get("variantResults", [])
        if not variants:
            print(f"  {name}: no treatment sessions scored yet")
        for vr in variants:
            sig = bool(vr.get("isSignificant"))
            pct = _percent_change(cs, vr)
            pct_str = f"{pct:+.1f}%" if pct is not None else "N/A"
            print(
                f"  {name}: change={pct_str}  significant={sig}  "
                f"n(control)={cs.get('sampleSize', '-')}  n(treatment)={vr.get('sampleSize', '-')}"
            )
            if sig and pct is not None and pct > 0:
                any_win = True
            elif sig and pct is not None and pct < 0:
                any_regression = True
    if any_regression:
        return "REGRESSION"
    if any_win:
        return "WIN"
    return "INCONCLUSIVE"


def stop_ab_test(ab_test_id: str):
    """Stop a running or paused A/B test and wait until it reports STOPPED."""
    try:
        ab = dp.get_ab_test(abTestId=ab_test_id)
        if ab.get("executionStatus") in ("RUNNING", "PAUSED"):
            dp.update_ab_test(abTestId=ab_test_id, executionStatus="STOPPED")
            for _ in range(20):
                ab = dp.get_ab_test(abTestId=ab_test_id)
                if ab.get("executionStatus") == "STOPPED":
                    break
                time.sleep(5)
        print(f"A/B test {ab_test_id}: executionStatus={ab.get('executionStatus')}")
    except Exception as e:
        print(f"Stop skipped: {e}")


# ── Cleanup function (also called with --cleanup-only) ────────────────────


# Deletes that follow an asynchronous change must wait for it to finish:
# an online eval config cannot be deleted while UPDATING, and a gateway cannot
# be deleted while any of its targets still exists (including DELETING ones).
CLEANUP_WAIT_TIMEOUT_SECONDS = 120
CLEANUP_WAIT_INTERVAL_SECONDS = 5


def _wait_online_eval_settled(ctrl_client, oe_id: str) -> str:
    """Wait until an online eval config leaves CREATING/UPDATING; return its status."""
    deadline = time.time() + CLEANUP_WAIT_TIMEOUT_SECONDS
    while True:
        status = ctrl_client.get_online_evaluation_config(onlineEvaluationConfigId=oe_id).get("status")
        if status not in ("CREATING", "UPDATING") or time.time() >= deadline:
            return status
        time.sleep(CLEANUP_WAIT_INTERVAL_SECONDS)


def _wait_gateway_targets_gone(ctrl_client, gateway_id: str) -> int:
    """Wait until a gateway lists no targets; return how many remain."""
    deadline = time.time() + CLEANUP_WAIT_TIMEOUT_SECONDS
    while True:
        remaining = len(ctrl_client.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", []))
        if remaining == 0 or time.time() >= deadline:
            return remaining
        time.sleep(CLEANUP_WAIT_INTERVAL_SECONDS)


def run_cleanup(state: dict):
    """Delete all AWS resources created during the workflow."""
    print("\n" + "=" * 60)
    print("CLEANUP")
    print("=" * 60)

    _dp = boto3.client("bedrock-agentcore", region_name=state.get("region", REGION))
    _ctrl = boto3.client("bedrock-agentcore-control", region_name=state.get("region", REGION))
    _logs = boto3.client("logs", region_name=state.get("region", REGION))

    # 1. Stop and delete A/B tests
    for ab_id, label in [
        (state.get("abtest_bundle_id"), "bundle"),
        (state.get("abtest_target_id"), "target"),
    ]:
        if not ab_id:
            continue
        print(f"1. Deleting A/B test ({label}): {ab_id}")
        try:
            ab = _dp.get_ab_test(abTestId=ab_id)
            if ab.get("executionStatus") in ("RUNNING", "PAUSED"):
                _dp.update_ab_test(abTestId=ab_id, executionStatus="STOPPED")
                time.sleep(3)
            _dp.delete_ab_test(abTestId=ab_id)
            print(f"   Deleted: {ab_id}")
        except Exception as e:
            print(f"   Skipped: {e}")

    # 2. Delete online evaluation configs
    for oe_id, label in [
        (state.get("online_eval_id"), "v1"),
        (state.get("online_eval_target_v1_id"), "v1 canary control"),
        (state.get("online_eval_v2_id"), "v2"),
    ]:
        if not oe_id:
            continue
        print(f"2. Deleting online eval config ({label}): {oe_id}")
        try:
            _wait_online_eval_settled(_ctrl, oe_id)
            _ctrl.update_online_evaluation_config(onlineEvaluationConfigId=oe_id, executionStatus="DISABLED")
            _wait_online_eval_settled(_ctrl, oe_id)
            _ctrl.delete_online_evaluation_config(onlineEvaluationConfigId=oe_id)
            print(f"   Deleted: {oe_id}")
        except Exception as e:
            print(f"   Skipped: {e}")

    # 3. Delete configuration bundles
    for b_id, label in [
        (state.get("baseline_bundle_id"), "baseline"),
        (state.get("control_bundle_id"), "control"),
        (state.get("treatment_bundle_id"), "treatment"),
    ]:
        if not b_id:
            continue
        print(f"3. Deleting bundle ({label}): {b_id}")
        try:
            _ctrl.delete_configuration_bundle(bundleId=b_id)
            print(f"   Deleted: {b_id}")
        except Exception as e:
            print(f"   Skipped: {e}")

    # 4. Delete gateway delivery and source
    delivery_id = state.get("delivery_id")
    if delivery_id:
        print(f"4a. Deleting delivery: {delivery_id}")
        try:
            _logs.delete_delivery(id=delivery_id)
            print(f"   Deleted delivery: {delivery_id}")
        except Exception as e:
            print(f"   Skipped delivery: {e}")

    delivery_source = state.get("delivery_source_name")
    if delivery_source:
        print(f"4b. Deleting delivery source: {delivery_source}")
        try:
            _logs.delete_delivery_source(name=delivery_source)
            print(f"   Deleted delivery source: {delivery_source}")
        except Exception as e:
            print(f"   Skipped delivery source: {e}")

    # 5. Delete gateway targets and gateway
    gateway_id = state.get("gateway_id")
    if gateway_id:
        for t_id, tname in [
            (state.get("target_id_v2"), "v2"),
            (state.get("target_id"), "v1"),
        ]:
            if not t_id:
                continue
            print(f"5. Deleting gateway target ({tname}): {t_id}")
            try:
                _ctrl.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t_id)
                print(f"   Deleted: {t_id}")
            except Exception as e:
                print(f"   Skipped: {e}")

        print(f"5. Deleting gateway: {gateway_id}")
        try:
            remaining = _wait_gateway_targets_gone(_ctrl, gateway_id)
            if remaining:
                print(f"   {remaining} target(s) still attached after {CLEANUP_WAIT_TIMEOUT_SECONDS}s; trying anyway")
            _ctrl.delete_gateway(gatewayIdentifier=gateway_id)
            print(f"   Deleted gateway: {gateway_id}")
        except Exception as e:
            print(f"   Skipped gateway: {e}")

    # 6. Delete AgentCore runtimes
    for rt_id, label in [
        (state.get("runtime_id_v2"), "v2"),
        (state.get("runtime_id"), "v1"),
    ]:
        if not rt_id:
            continue
        print(f"6. Deleting runtime ({label}): {rt_id}")
        try:
            _ctrl.delete_agent_runtime(agentRuntimeId=rt_id)
            print(f"   Deleted runtime: {rt_id}")
        except Exception as e:
            print(f"   Skipped runtime: {e}")

    # 7. Delete IAM roles
    for role_name, label in [
        (state.get("role_name_v2"), "v2"),
        (state.get("role_name"), "v1"),
    ]:
        if not role_name:
            continue
        print(f"7. Deleting IAM role ({label}): {role_name}")
        try:
            iam = boto3.client("iam")
            for policy in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role_name, PolicyName=policy)
            iam.delete_role(RoleName=role_name)
            print(f"   Deleted: {role_name}")
        except Exception as e:
            print(f"   Skipped: {e}")

    print("\nCleanup complete.")


# ── Load cleanup state if --cleanup-only ──────────────────────────────────

if args.cleanup_only:
    cleanup_state_file = Path(f"optimize_state_{V1_NAME}.json")
    if cleanup_state_file.exists():
        cleanup_state = json.loads(cleanup_state_file.read_text())
        run_cleanup(cleanup_state)
    else:
        print(f"No state file found at {cleanup_state_file}. Nothing to clean up.")
    sys.exit(0)

# ── Step 2: Deploy v1 (or load existing state) ────────────────────────────

print("\n" + "=" * 60)
print("STEP 2: Deploy HR Assistant v1")
print("=" * 60)

v1_state_file = Path(f"agent_state_{V1_NAME}.json")
if args.skip_deploy and v1_state_file.exists():
    print(f"Loading existing state from {v1_state_file}")
    v1_state = json.loads(v1_state_file.read_text())
else:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "deploy.py"),
            "--name",
            V1_NAME,
            "--region",
            REGION,
            "--version",
            "v1",
        ],
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"deploy.py failed with exit code {result.returncode}")
    v1_state = json.loads(v1_state_file.read_text())

AGENT_ARN = v1_state["runtime_arn"]
LOG_GROUP = v1_state["log_group"]
SERVICE_NAME = v1_state["service_name"]
ROLE_ARN = v1_state["role_arn"]
ROLE_NAME = v1_state["role_name"]
S3_BUCKET = v1_state["s3_bucket"]

LOG_GROUP_ARN = f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:{LOG_GROUP}"
SPANS_LOG_ARN = f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:{SPANS_LOG_GROUP}"

print(f"Agent ARN    : {AGENT_ARN}")
print(f"Log Group    : {LOG_GROUP}")
print(f"Service Name : {SERVICE_NAME}")

# ── Step 3: Create baseline bundle and send traffic ───────────────────────

print("\n" + "=" * 60)
print("STEP 3: Create Baseline Configuration Bundle & Send Traffic")
print("=" * 60)

baseline_resp = ctrl.create_configuration_bundle(
    bundleName=f"HRBaseline{SUFFIX}",
    description="HR Assistant baseline configuration",
    components={
        AGENT_ARN: {
            "configuration": {
                "system_prompt": CURRENT_SYSTEM_PROMPT,
                "tool_descriptions": CURRENT_TOOL_DESCRIPTIONS,
            }
        }
    },
    commitMessage="Initial configuration — baseline system prompt and tool descriptions",
    clientToken=str(uuid.uuid4()),
)
BASELINE_BUNDLE_ARN = baseline_resp["bundleArn"]
BASELINE_BUNDLE_VERSION = baseline_resp["versionId"]
BASELINE_BUNDLE_ID = baseline_resp["bundleId"]

baseline_baggage = (
    f"aws.agentcore.configbundle_arn={BASELINE_BUNDLE_ARN},aws.agentcore.configbundle_version={BASELINE_BUNDLE_VERSION}"
)
print(f"Baseline bundle ID : {BASELINE_BUNDLE_ID}")

BASELINE_PROMPTS = [
    ("EMP-001", "What is my current PTO balance?"),
    (
        "EMP-001",
        "Please submit a PTO request for me from 2026-06-01 to 2026-06-05 for a family vacation.",
    ),
    ("EMP-001", "Can you pull up my January 2026 pay stub?"),
    ("EMP-002", "How many PTO days do I have left? I only joined recently."),
    ("EMP-042", "What's the company policy on working from home?"),
    (
        "EMP-001",
        "What are my health insurance options and how much does the company cover?",
    ),
    ("EMP-042", "Tell me about the 401k plan — how much does the company match?"),
    ("EMP-001", "What is the parental leave policy for primary caregivers?"),
    (
        "EMP-002",
        "I want to request time off from 2026-07-14 to 2026-07-18 for a medical procedure.",
    ),
    (
        "EMP-042",
        "Can you show me my December 2025 pay stub and explain the deductions?",
    ),
]

baseline_session_ids = []
for emp_id, prompt in BASELINE_PROMPTS:
    session_id = str(uuid.uuid4())
    baseline_session_ids.append(session_id)
    full_prompt = f"Employee ID: {emp_id}. {prompt}"
    resp = dp.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": full_prompt}).encode(),
        baggage=baseline_baggage,
    )
    resp["response"].read()
    print(f"Session {session_id[:8]}... [{emp_id}] {prompt[:55]}")

print(f"\nSent {len(baseline_session_ids)} baseline sessions.")
print("Waiting 3 minutes for CloudWatch ingestion...")
for remaining in range(180, 0, -30):
    print(f"  {remaining}s remaining...")
    time.sleep(30)
print("CloudWatch ingestion complete.")

# ── Step 4: Baseline batch evaluation ─────────────────────────────────────

print("\n" + "=" * 60)
print("STEP 4: Baseline Batch Evaluation")
print("=" * 60)

eval_resp = dp.start_batch_evaluation(
    batchEvaluationName=f"HRBaseline{SUFFIX}",
    evaluators=[
        {"evaluatorId": "Builtin.GoalSuccessRate"},
        {"evaluatorId": "Builtin.Helpfulness"},
        {"evaluatorId": "Builtin.Correctness"},
    ],
    dataSourceConfig={
        "cloudWatchLogs": {
            "serviceNames": [SERVICE_NAME],
            "logGroupNames": [SPANS_LOG_GROUP, LOG_GROUP],
            "filterConfig": {"sessionIds": baseline_session_ids},
        }
    },
    clientToken=str(uuid.uuid4()),
)
BASELINE_EVAL_ID = eval_resp["batchEvaluationId"]
print(f"Started batch evaluation: {BASELINE_EVAL_ID}")
print("Polling for completion...")

TERMINAL = {"COMPLETED", "FAILED", "STOPPED", "COMPLETED_WITH_ERRORS"}
while True:
    result = dp.get_batch_evaluation(batchEvaluationId=BASELINE_EVAL_ID)
    status = result["status"]
    print(f"  Status: {status}")
    if status in TERMINAL:
        break
    time.sleep(30)

baseline_scores = {}
er = result.get("evaluationResults", {})
for s in er.get("evaluatorSummaries", []):
    avg = s.get("statistics", {}).get("averageScore")
    if avg is not None:
        baseline_scores[s["evaluatorId"]] = avg

if not baseline_scores:
    print("  Reading scores from CloudWatch...")
    baseline_scores = fetch_eval_scores(BASELINE_EVAL_ID)

print(f"\n{'Evaluator':<35} {'Score':>8}")
print("-" * 45)
for eid, score in sorted(baseline_scores.items()):
    print(f"{eid:<35} {score:>8.4f}")

# ── Step 5a: System prompt recommendation ─────────────────────────────────

print("\n" + "=" * 60)
print("STEP 5a: System Prompt Recommendation")
print("=" * 60)

now = datetime.now(timezone.utc)
start_dt = now - timedelta(days=7)

sp_rec_resp = dp.start_recommendation(
    name=f"HRSpRec{SUFFIX}",
    type="SYSTEM_PROMPT_RECOMMENDATION",
    recommendationConfig={
        "systemPromptRecommendationConfig": {
            "systemPrompt": {"text": CURRENT_SYSTEM_PROMPT},
            "agentTraces": {
                "cloudwatchLogs": {
                    "logGroupArns": [LOG_GROUP_ARN],
                    "serviceNames": [SERVICE_NAME],
                    "startTime": start_dt,
                    "endTime": now,
                }
            },
            "evaluationConfig": {
                "evaluators": [{"evaluatorArn": "arn:aws:bedrock-agentcore:::evaluator/Builtin.GoalSuccessRate"}]
            },
        }
    },
    clientToken=str(uuid.uuid4()),
)
SP_REC_ID = sp_rec_resp["recommendationId"]
print(f"Started system prompt recommendation: {SP_REC_ID}")

REC_TERMINAL = {"COMPLETED", "FAILED"}
while True:
    sp_result = dp.get_recommendation(recommendationId=SP_REC_ID)
    status = sp_result["status"]
    print(f"  Status: {status}")
    if status in REC_TERMINAL:
        break
    time.sleep(30)

rec = sp_result.get("recommendationResult", {})
sp_rec_result = rec.get("systemPromptRecommendationResult", {})
RECOMMENDED_SYSTEM_PROMPT = sp_rec_result.get("recommendedSystemPrompt") or CURRENT_SYSTEM_PROMPT

if sp_rec_result.get("errorCode"):
    print(f"Recommendation error ({sp_rec_result['errorCode']}): {sp_rec_result.get('errorMessage', '')[:200]}")
    print("Falling back to current system prompt.")

print("\n" + "=" * 60)
print("RECOMMENDED SYSTEM PROMPT")
print("=" * 60)
print(RECOMMENDED_SYSTEM_PROMPT)

# ── Step 5b: Tool description recommendation ──────────────────────────────

print("\n" + "=" * 60)
print("STEP 5b: Tool Description Recommendation")
print("=" * 60)

tools_list = [{"toolName": name, "toolDescription": {"text": desc}} for name, desc in CURRENT_TOOL_DESCRIPTIONS.items()]

td_rec_resp = dp.start_recommendation(
    name=f"HRTdRec{SUFFIX}",
    type="TOOL_DESCRIPTION_RECOMMENDATION",
    recommendationConfig={
        "toolDescriptionRecommendationConfig": {
            "toolDescription": {"toolDescriptionText": {"tools": tools_list}},
            "agentTraces": {
                "cloudwatchLogs": {
                    "logGroupArns": [LOG_GROUP_ARN],
                    "serviceNames": [SERVICE_NAME],
                    "startTime": start_dt,
                    "endTime": now,
                }
            },
        }
    },
    clientToken=str(uuid.uuid4()),
)
TD_REC_ID = td_rec_resp["recommendationId"]
print(f"Started tool description recommendation: {TD_REC_ID}")

while True:
    td_result = dp.get_recommendation(recommendationId=TD_REC_ID)
    status = td_result["status"]
    print(f"  Status: {status}")
    if status in REC_TERMINAL:
        break
    time.sleep(30)

RECOMMENDED_TOOL_DESCRIPTIONS = dict(CURRENT_TOOL_DESCRIPTIONS)

if status == "COMPLETED":
    td_rec_result = td_result.get("recommendationResult", {}).get("toolDescriptionRecommendationResult", {})
    returned_tools = td_rec_result.get("tools", [])
    tool_keys = list(CURRENT_TOOL_DESCRIPTIONS.keys())

    if td_rec_result.get("errorCode"):
        print(f"Recommendation error: {td_rec_result.get('errorMessage', '')[:200]}")
    elif returned_tools:
        print("\n" + "=" * 60)
        print("RECOMMENDED TOOL DESCRIPTIONS")
        print("=" * 60)
        for i, item in enumerate(returned_tools):
            new_desc = item.get("recommendedToolDescription", "")
            tool_name = item.get("toolName") or (tool_keys[i] if i < len(tool_keys) else f"tool_{i}")
            RECOMMENDED_TOOL_DESCRIPTIONS[tool_name] = new_desc
            print(f"\n[{tool_name}]")
            print(f"  Before: {CURRENT_TOOL_DESCRIPTIONS.get(tool_name, '(unknown)')}")
            print(f"  After : {new_desc}")
    else:
        print("No tool description recommendations returned. Using current descriptions.")

# ── Step 6: Create control and treatment bundles ───────────────────────────

print("\n" + "=" * 60)
print("STEP 6: Configuration Bundles — Control and Treatment")
print("=" * 60)

control_resp = ctrl.create_configuration_bundle(
    bundleName=f"HRControl{SUFFIX}",
    description="HR Assistant control variant — original system prompt and tool descriptions",
    components={
        AGENT_ARN: {
            "configuration": {
                "system_prompt": CURRENT_SYSTEM_PROMPT,
                "tool_descriptions": CURRENT_TOOL_DESCRIPTIONS,
            }
        }
    },
    commitMessage="Control: original system prompt and tool descriptions (v1 baseline)",
    clientToken=str(uuid.uuid4()),
)
CONTROL_BUNDLE_ARN = control_resp["bundleArn"]
CONTROL_BUNDLE_VERSION = control_resp["versionId"]
CONTROL_BUNDLE_ID = control_resp["bundleId"]
print(f"Control bundle ID      : {CONTROL_BUNDLE_ID}")

treatment_resp = ctrl.create_configuration_bundle(
    bundleName=f"HRTreatment{SUFFIX}",
    description="HR Assistant treatment variant — recommended system prompt and tool descriptions",
    components={
        AGENT_ARN: {
            "configuration": {
                "system_prompt": RECOMMENDED_SYSTEM_PROMPT,
                "tool_descriptions": RECOMMENDED_TOOL_DESCRIPTIONS,
            }
        }
    },
    commitMessage="Treatment: AI-recommended system prompt + improved tool descriptions from Step 5",
    clientToken=str(uuid.uuid4()),
)
TREATMENT_BUNDLE_ARN = treatment_resp["bundleArn"]
TREATMENT_BUNDLE_VERSION = treatment_resp["versionId"]
TREATMENT_BUNDLE_ID = treatment_resp["bundleId"]
print(f"Treatment bundle ID    : {TREATMENT_BUNDLE_ID}")

# Read and verify treatment bundle
read_resp = ctrl.get_configuration_bundle(bundleId=TREATMENT_BUNDLE_ID)
config = read_resp["components"][AGENT_ARN]["configuration"]
print("\nTreatment bundle verified:")
print(f"  System prompt (first 100 chars): {config['system_prompt'][:100]}...")
print(f"  Tool descriptions: {len(config.get('tool_descriptions', {}))} tools")

# Compare control vs treatment
v_control = ctrl.get_configuration_bundle_version(bundleId=CONTROL_BUNDLE_ID, versionId=CONTROL_BUNDLE_VERSION)
v_treatment = ctrl.get_configuration_bundle_version(bundleId=TREATMENT_BUNDLE_ID, versionId=TREATMENT_BUNDLE_VERSION)
cfg_c = v_control["components"][AGENT_ARN]["configuration"]
cfg_t = v_treatment["components"][AGENT_ARN]["configuration"]
print("\nControl vs Treatment diff:")
for key in sorted(set(cfg_c.keys()) | set(cfg_t.keys())):
    if cfg_c.get(key) != cfg_t.get(key):
        print(f"  [{key}] CHANGED")
print(f"Control commitMessage  : {v_control.get('commitMessage')}")
print(f"Treatment commitMessage: {v_treatment.get('commitMessage')}")

# ── Step 7: A/B test — config-bundle routing ──────────────────────────────

print("\n" + "=" * 60)
print("STEP 7: A/B Test — Configuration Bundle Routing")
print("=" * 60)

# 7a: Create gateway
gw_resp = ctrl.create_gateway(
    name=f"HRGateway{SUFFIX}",
    description="HR Assistant A/B test gateway",
    authorizerType="AWS_IAM",
    roleArn=ROLE_ARN,
    clientToken=str(uuid.uuid4()),
)
GATEWAY_ID = gw_resp["gatewayId"]
print(f"Gateway created: {GATEWAY_ID}. Polling for READY...")

for i in range(30):
    gw = ctrl.get_gateway(gatewayIdentifier=GATEWAY_ID)
    if gw.get("status") == "READY":
        break
    print(f"  Poll {i + 1}: {gw.get('status')}")
    time.sleep(5)

GATEWAY_ARN = gw.get("gatewayArn") or f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:gateway/{GATEWAY_ID}"
GATEWAY_URL = gw.get("gatewayUrl") or f"https://{GATEWAY_ID}.gateway.bedrock-agentcore.{REGION}.amazonaws.com"
print(f"GATEWAY_URL = {GATEWAY_URL}")

# 7a: Create v1 gateway target
tgt_resp = ctrl.create_gateway_target(
    gatewayIdentifier=GATEWAY_ID,
    name=TARGET_NAME,
    description="HR Assistant v1 runtime target",
    targetConfiguration={"http": {"agentcoreRuntime": {"arn": AGENT_ARN, "qualifier": "DEFAULT"}}},
    credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    clientToken=str(uuid.uuid4()),
)
TARGET_ID = tgt_resp["targetId"]
for i in range(30):
    tgt = ctrl.get_gateway_target(gatewayIdentifier=GATEWAY_ID, targetId=TARGET_ID)
    if tgt.get("status") == "READY":
        break
    print(f"  Target poll {i + 1}: {tgt.get('status')}")
    time.sleep(5)
print(f"Target READY: {TARGET_ID}")

# 7b: Configure gateway tracing
dest = xray.get_trace_segment_destination()
if dest.get("Destination") != "CloudWatchLogs":
    xray.update_trace_segment_destination(Destination="CloudWatchLogs")
    print("X-Ray trace destination set to CloudWatchLogs")
else:
    print("X-Ray trace destination already CloudWatchLogs")

try:
    logs.put_delivery_source(
        name=DELIVERY_SOURCE_NAME,
        resourceArn=GATEWAY_ARN,
        logType="TRACES",
    )
    print(f"Delivery source created: {DELIVERY_SOURCE_NAME}")
except Exception as e:
    print(f"Delivery source: {e}")

destinations = logs.describe_delivery_destinations().get("deliveryDestinations", [])
xray_dest = next((d for d in destinations if d.get("deliveryDestinationType") == "XRAY"), None)
if not xray_dest:
    logs.put_delivery_destination(
        name="xray-destination",
        deliveryDestinationType="XRAY",
    )
    destinations = logs.describe_delivery_destinations().get("deliveryDestinations", [])
    xray_dest = next((d for d in destinations if d.get("deliveryDestinationType") == "XRAY"), None)

XRAY_DEST_ARN = xray_dest["arn"]
try:
    delivery = logs.create_delivery(
        deliverySourceName=DELIVERY_SOURCE_NAME,
        deliveryDestinationArn=XRAY_DEST_ARN,
    )
    DELIVERY_ID = delivery["delivery"]["id"]
    print(f"Delivery created: {DELIVERY_ID}")
except Exception as e:
    print(f"Delivery: {e}")
    for d in logs.describe_deliveries().get("deliveries", []):
        if d.get("deliverySourceName") == DELIVERY_SOURCE_NAME:
            DELIVERY_ID = d.get("id")
            break

# 7c: Create online evaluation config
online_eval_resp = ctrl.create_online_evaluation_config(
    onlineEvaluationConfigName=f"HROnlineEval{SUFFIX}",
    description="HR Assistant online evaluation for A/B testing",
    dataSourceConfig={
        "cloudWatchLogs": {
            "logGroupNames": [LOG_GROUP],
            "serviceNames": [SERVICE_NAME],
        }
    },
    evaluators=[
        {"evaluatorId": "Builtin.GoalSuccessRate"},
        {"evaluatorId": "Builtin.Helpfulness"},
    ],
    rule={
        "samplingConfig": {"samplingPercentage": 100.0},
        "sessionConfig": {"sessionTimeoutMinutes": 2},
    },
    evaluationExecutionRoleArn=ROLE_ARN,
    enableOnCreate=True,
    clientToken=str(uuid.uuid4()),
)
ONLINE_EVAL_ID = online_eval_resp["onlineEvaluationConfigId"]
ONLINE_EVAL_ARN = online_eval_resp["onlineEvaluationConfigArn"]
print(f"Online eval config: {ONLINE_EVAL_ID}")

# 7d: Create config-bundle A/B test
abtest_resp = dp.create_ab_test(
    name=f"HRBundleAB{SUFFIX}",
    description="HR Assistant: compare original vs recommended system prompt",
    gatewayArn=GATEWAY_ARN,
    roleArn=ROLE_ARN,
    enableOnCreate=True,
    evaluationConfig={"onlineEvaluationConfigArn": ONLINE_EVAL_ARN},
    variants=[
        {
            "name": "C",
            "weight": 50,
            "variantConfiguration": {
                "configurationBundle": {
                    "bundleArn": CONTROL_BUNDLE_ARN,
                    "bundleVersion": CONTROL_BUNDLE_VERSION,
                }
            },
        },
        {
            "name": "T1",
            "weight": 50,
            "variantConfiguration": {
                "configurationBundle": {
                    "bundleArn": TREATMENT_BUNDLE_ARN,
                    "bundleVersion": TREATMENT_BUNDLE_VERSION,
                }
            },
        },
    ],
    clientToken=str(uuid.uuid4()),
)
ABTEST_BUNDLE_ID = abtest_resp["abTestId"]
print(f"Config-bundle A/B test created: {ABTEST_BUNDLE_ID}")

for i in range(30):
    ab = dp.get_ab_test(abTestId=ABTEST_BUNDLE_ID)
    s, es = ab.get("status", ""), ab.get("executionStatus", "")
    print(f"  Poll {i + 1}: status={s}  executionStatus={es}")
    if s == "ACTIVE" and es == "RUNNING":
        break
    if "FAILED" in s:
        print(f"  Error: {ab.get('errorDetails')}")
        break
    time.sleep(5)

# 7e: Send traffic through gateway
print("\nSending traffic through gateway (SigV4-signed)...")
session = boto3.Session()
credentials = session.get_credentials().get_frozen_credentials()
GW_INVOKE_URL = f"{GATEWAY_URL}/{TARGET_NAME}/invocations"

GW_PROMPTS = [
    "Employee ID: EMP-001. What is my current PTO balance?",
    "Employee ID: EMP-001. I need to request leave from 2026-08-04 to 2026-08-08 for a vacation.",
    "Employee ID: EMP-042. Can you explain our 401k matching policy?",
    "Employee ID: EMP-002. I only have a few days left. What exactly is the PTO rollover policy?",
    "Employee ID: EMP-001. Show me my January 2026 pay stub and explain the deductions.",
    "Employee ID: EMP-042. What are my health insurance options?",
    "Employee ID: EMP-001. What's the remote work policy at Acme?",
    "Employee ID: EMP-002. I need to take parental leave soon. How many weeks am I entitled to?",
    "Employee ID: EMP-042. Please submit a PTO request for 2026-09-01 to 2026-09-03 for personal reasons.",
    "Employee ID: EMP-001. How much life insurance does the company provide?",
]

gw_session_ids = []
success, fail = 0, 0
for i, prompt in enumerate(GW_PROMPTS):
    sid = str(uuid.uuid4())
    gw_session_ids.append(sid)
    body = json.dumps({"prompt": prompt, "sessionId": sid})
    req = AWSRequest(
        method="POST",
        url=GW_INVOKE_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sid,
        },
    )
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(req)
    try:
        resp = http_requests.post(GW_INVOKE_URL, data=body, headers=dict(req.headers), timeout=120)
        if resp.status_code == 200:
            success += 1
            print(f"  [{i + 1:2d}] OK  {sid[:8]}...")
        else:
            fail += 1
            print(f"  [{i + 1:2d}] ERR {resp.status_code}: {resp.text[:80]}")
    except Exception as e:
        fail += 1
        print(f"  [{i + 1:2d}] ERR {e}")
    time.sleep(1)

bundle_last_request_at = datetime.now(timezone.utc)  # noqa: UP017 - datetime.UTC requires Python 3.11; this sample supports 3.10+
print(f"\nGateway traffic: success={success}, fail={fail}")

# 7f: Poll for A/B test results
print("\nPolling for config-bundle A/B test results (up to 25 minutes)...")
bundle_ab_results = poll_ab_results(
    ABTEST_BUNDLE_ID,
    not_before=bundle_last_request_at + timedelta(minutes=SESSION_TIMEOUT_MINUTES),
    control_label="Control",
    treatment_label="Treatment",
)

# 7g: Stop the test before acting on its results. Only one A/B test can run
# per gateway, and Step 8 starts a new one on the same gateway.
print("\nStopping config-bundle A/B test...")
stop_ab_test(ABTEST_BUNDLE_ID)

# 7h: Decide and promote. Promotion happens only when the treatment is
# significantly better on at least one evaluator and worse on none.
print("\n--- Config-bundle A/B decision ---")
bundle_decision = decide_ab_outcome(bundle_ab_results) if bundle_ab_results else "NO_RESULTS"
print(f"Decision: {bundle_decision}")

if bundle_decision == "WIN":
    # Promote the treatment config into the control bundle as a new version.
    # parentVersionIds records lineage and rejects the update if someone else
    # changed the bundle since we read it.
    current = ctrl.get_configuration_bundle(bundleId=CONTROL_BUNDLE_ID)
    CONTROL_BUNDLE_VERSION = current["versionId"]
    promote_resp = ctrl.update_configuration_bundle(
        bundleId=CONTROL_BUNDLE_ID,
        components={
            AGENT_ARN: {
                "configuration": {
                    "system_prompt": RECOMMENDED_SYSTEM_PROMPT,
                    "tool_descriptions": RECOMMENDED_TOOL_DESCRIPTIONS,
                }
            }
        },
        parentVersionIds=[CONTROL_BUNDLE_VERSION],
        commitMessage="Promote treatment: AI-recommended prompt + tool descriptions (A/B validated)",
        clientToken=str(uuid.uuid4()),
    )
    CONTROL_BUNDLE_VERSION_V2 = promote_resp["versionId"]
    print(f"ACTION: Treatment won. Control bundle promoted to version: {CONTROL_BUNDLE_VERSION_V2}")
elif bundle_decision == "REGRESSION":
    print("ACTION: Treatment is significantly worse. Keeping the control bundle; investigate the treatment.")
elif bundle_decision == "INCONCLUSIVE":
    print("ACTION: No significant difference (p >= 0.05). Keeping the control bundle unchanged.")
    print("        Collect more sessions before promoting. With ~10 sessions this is the expected outcome.")
    print("        Once T1 wins, promotion is one update_configuration_bundle call on the control bundle")
    print("        with the treatment config and parentVersionIds=[<current control version>].")
else:
    print("ACTION: No fresh results yet. Keeping the control bundle unchanged.")

# ── Step 8: A/B test — target-based routing ───────────────────────────────

print("\n" + "=" * 60)
print("STEP 8: A/B Test — Target-Based Routing (Phased Rollout)")
print("=" * 60)

# 8a: Deploy v2
v2_state_file = Path(f"agent_state_{V2_NAME}.json")
if args.skip_deploy and v2_state_file.exists():
    print(f"Loading existing v2 state from {v2_state_file}")
    v2_state = json.loads(v2_state_file.read_text())
else:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "deploy.py"),
            "--name",
            V2_NAME,
            "--region",
            REGION,
            "--version",
            "v2",
        ],
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"deploy.py (v2) failed with exit code {result.returncode}")
    v2_state = json.loads(v2_state_file.read_text())

AGENT_ARN_V2 = v2_state["runtime_arn"]
LOG_GROUP_V2 = v2_state["log_group"]
SERVICE_NAME_V2 = v2_state["service_name"]
ROLE_ARN_V2 = v2_state["role_arn"]
print(f"v2 Agent ARN : {AGENT_ARN_V2}")

# 8b: Add v2 gateway target
tgt_v2_resp = ctrl.create_gateway_target(
    gatewayIdentifier=GATEWAY_ID,
    name=TARGET_NAME_V2,
    description="HR Assistant v2 runtime target (escalation tool + improved prompt)",
    targetConfiguration={"http": {"agentcoreRuntime": {"arn": AGENT_ARN_V2, "qualifier": "DEFAULT"}}},
    credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    clientToken=str(uuid.uuid4()),
)
TARGET_ID_V2 = tgt_v2_resp["targetId"]
for i in range(30):
    tgt_v2 = ctrl.get_gateway_target(gatewayIdentifier=GATEWAY_ID, targetId=TARGET_ID_V2)
    if tgt_v2.get("status") == "READY":
        break
    print(f"  v2 target poll {i + 1}: {tgt_v2.get('status')}")
    time.sleep(5)
print(f"v2 target READY: {TARGET_ID_V2}")

# 8c: Create online eval config for v2
online_eval_v2_resp = ctrl.create_online_evaluation_config(
    onlineEvaluationConfigName=f"HROnlineEvalV2{SUFFIX}",
    description="HR Assistant v2 online evaluation (target-based routing)",
    dataSourceConfig={
        "cloudWatchLogs": {
            "logGroupNames": [LOG_GROUP_V2],
            "serviceNames": [SERVICE_NAME_V2],
        }
    },
    evaluators=[
        {"evaluatorId": "Builtin.GoalSuccessRate"},
        {"evaluatorId": "Builtin.Helpfulness"},
    ],
    rule={
        "samplingConfig": {"samplingPercentage": 100.0},
        "sessionConfig": {"sessionTimeoutMinutes": 2},
    },
    evaluationExecutionRoleArn=ROLE_ARN_V2,
    enableOnCreate=True,
    clientToken=str(uuid.uuid4()),
)
ONLINE_EVAL_V2_ID = online_eval_v2_resp["onlineEvaluationConfigId"]
ONLINE_EVAL_V2_ARN = online_eval_v2_resp["onlineEvaluationConfigArn"]
print(f"v2 online eval config: {ONLINE_EVAL_V2_ID}")

# 8c (cont.): Create a dedicated online eval config for the v1 (control) side.
# Variant names are fixed to "C" and "T1" by the API, so the only way to keep
# this test's control scores separate from the Step 7 test is to give it its
# own online eval config. Reusing the Step 7 config returned that test's
# results unchanged for this one.
online_eval_tgt_v1_resp = ctrl.create_online_evaluation_config(
    onlineEvaluationConfigName=f"HROnlineEvalTgtV1{SUFFIX}",
    description="HR Assistant v1 online evaluation (control side of target-based routing)",
    dataSourceConfig={
        "cloudWatchLogs": {
            "logGroupNames": [LOG_GROUP],
            "serviceNames": [SERVICE_NAME],
        }
    },
    evaluators=[
        {"evaluatorId": "Builtin.GoalSuccessRate"},
        {"evaluatorId": "Builtin.Helpfulness"},
    ],
    rule={
        "samplingConfig": {"samplingPercentage": 100.0},
        "sessionConfig": {"sessionTimeoutMinutes": SESSION_TIMEOUT_MINUTES},
    },
    evaluationExecutionRoleArn=ROLE_ARN,
    enableOnCreate=True,
    clientToken=str(uuid.uuid4()),
)
ONLINE_EVAL_TARGET_V1_ID = online_eval_tgt_v1_resp["onlineEvaluationConfigId"]
ONLINE_EVAL_TARGET_V1_ARN = online_eval_tgt_v1_resp["onlineEvaluationConfigArn"]
print(f"v1 canary-control online eval config: {ONLINE_EVAL_TARGET_V1_ID}")

# 8d: Create target-based A/B test (the Step 7 test was already stopped in 7g)
abtest_target_resp = dp.create_ab_test(
    name=f"HRTargetAB{SUFFIX}",
    description="HR Assistant: phased rollout v1 (90%) vs v2 (10%)",
    gatewayArn=GATEWAY_ARN,
    roleArn=ROLE_ARN,
    enableOnCreate=True,
    evaluationConfig={
        "perVariantOnlineEvaluationConfig": [
            {"name": "C", "onlineEvaluationConfigArn": ONLINE_EVAL_TARGET_V1_ARN},
            {"name": "T1", "onlineEvaluationConfigArn": ONLINE_EVAL_V2_ARN},
        ]
    },
    gatewayFilter={"targetPaths": [f"/{TARGET_NAME}/*"]},
    variants=[
        {
            "name": "C",
            "weight": 90,
            "variantConfiguration": {"target": {"name": TARGET_NAME}},
        },
        {
            "name": "T1",
            "weight": 10,
            "variantConfiguration": {"target": {"name": TARGET_NAME_V2}},
        },
    ],
    clientToken=str(uuid.uuid4()),
)
ABTEST_TARGET_ID = abtest_target_resp["abTestId"]
print(f"Target-based A/B test: {ABTEST_TARGET_ID}")

for i in range(30):
    ab = dp.get_ab_test(abTestId=ABTEST_TARGET_ID)
    s, es = ab.get("status", ""), ab.get("executionStatus", "")
    print(f"  Poll {i + 1}: status={s}  executionStatus={es}")
    if s == "ACTIVE" and es == "RUNNING":
        break
    if "FAILED" in s:
        raise RuntimeError("Failed to create target A/B test")
    time.sleep(5)

print(f"\n90% -> {TARGET_NAME} (v1)   10% -> {TARGET_NAME_V2} (v2 canary)")

# 8e: Send traffic. Every request goes to the control target's path
# (/HRAgentV1/invocations). The A/B test's gatewayFilter intercepts only that
# path, and the gateway splits each new session 90/10 between v1 and v2.
# Calling /HRAgentV2/invocations directly would bypass the split entirely.
# 20 sessions keeps the step short; expect roughly 18 on v1 and 2 on v2, which
# shows the routing but is far too few for statistical significance.
TARGET_PROMPTS = [
    "Employee ID: EMP-001. Check my PTO balance and submit a request for 2026-11-24 to 2026-11-28.",
    "Employee ID: EMP-042. I have a payroll dispute. Can you escalate this to an HR manager?",
    "Employee ID: EMP-002. What benefits can I enroll in during open enrollment?",
    "Employee ID: EMP-001. What's the maximum PTO carryover allowed?",
    "Employee ID: EMP-042. My manager is creating a hostile work environment. I need help.",
    "Employee ID: EMP-001. How many weeks of parental leave will I get as a primary caregiver?",
    "Employee ID: EMP-002. Pull up my pay stub for January 2026.",
    "Employee ID: EMP-001. Can I take PTO before I've fully accrued the days?",
    "Employee ID: EMP-042. I need a dental claim reviewed — can you escalate?",
    "Employee ID: EMP-001. What vision insurance benefits do we have?",
    "Employee ID: EMP-002. What is my current PTO balance?",
    "Employee ID: EMP-042. My December 2025 pay stub looks wrong. Who can review it?",
    "Employee ID: EMP-001. What does the code of conduct say about outside employment?",
    "Employee ID: EMP-002. Submit a PTO request from 2026-10-05 to 2026-10-07 for a family event.",
    "Employee ID: EMP-042. How much does the company contribute to my 401k?",
    "Employee ID: EMP-001. I was denied a remote work request. Can someone in HR look at it?",
    "Employee ID: EMP-002. What life insurance coverage do I get?",
    "Employee ID: EMP-042. Can I work from another country for two weeks?",
    "Employee ID: EMP-001. Show me my January 2026 pay stub and explain the 401k deduction.",
    "Employee ID: EMP-002. I need to report a safety concern at the office. What should I do?",
]

print(f"Sending {len(TARGET_PROMPTS)} sessions to /{TARGET_NAME}/invocations (gateway applies the 90/10 split)...")
success, fail = 0, 0
for i, prompt in enumerate(TARGET_PROMPTS):
    sid = str(uuid.uuid4())
    body = json.dumps({"prompt": prompt, "sessionId": sid})
    req = AWSRequest(
        method="POST",
        url=GW_INVOKE_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sid,
        },
    )
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(req)
    try:
        resp = http_requests.post(GW_INVOKE_URL, data=body, headers=dict(req.headers), timeout=120)
        if resp.status_code == 200:
            success += 1
            print(f"  [{i + 1:2d}] OK  {sid[:8]}...")
        else:
            fail += 1
            print(f"  [{i + 1:2d}] ERR {resp.status_code}: {resp.text[:80]}")
    except Exception as e:
        fail += 1
        print(f"  [{i + 1:2d}] ERR {e}")
    time.sleep(1)

target_last_request_at = datetime.now(timezone.utc)  # noqa: UP017 - datetime.UTC requires Python 3.11; this sample supports 3.10+
print(f"\nTarget traffic: success={success}, fail={fail}")

# 8f: Poll for target A/B results. The n= values show how the gateway split
# the sessions between v1 (C) and v2 (T1).
print("\nPolling for target-based A/B test results (up to 25 minutes)...")
target_ab_results = poll_ab_results(
    ABTEST_TARGET_ID,
    not_before=target_last_request_at + timedelta(minutes=SESSION_TIMEOUT_MINUTES),
    control_label="v1",
    treatment_label="v2",
)

# 8g: Rollout decision (README "Phased Rollout"). The test keeps running so the
# canary can collect more sessions; cleanup.py stops and deletes it.
print("\n--- Target-based A/B decision ---")
target_decision = decide_ab_outcome(target_ab_results) if target_ab_results else "NO_RESULTS"
print(f"Decision: {target_decision}")
if target_decision == "WIN":
    print("ACTION: v2 is significantly better. Ramp to 50% (update_ab_test with new weights),")
    print("        then stop the test and route all traffic to v2.")
elif target_decision == "REGRESSION":
    print("ACTION: v2 is significantly worse. Halt the rollout: stop the test and keep v1.")
elif target_decision == "INCONCLUSIVE":
    print("ACTION: No significant difference yet. Keep the 90/10 split and keep sending traffic.")
    print("        A ~2-session canary cannot reach significance; this is the expected outcome here.")
else:
    print("ACTION: No fresh results yet. Keep the canary running and check get_ab_test later.")

print("\n" + "=" * 60)
print("Optimization workflow complete.")
print(f"Run 'python cleanup.py --name {V1_NAME}' to remove all AWS resources.")
print("=" * 60)
