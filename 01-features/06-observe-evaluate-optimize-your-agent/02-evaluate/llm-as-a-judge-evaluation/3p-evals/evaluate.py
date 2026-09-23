"""
Third-party evaluators for the HR Assistant agent.

AgentCore Evaluations can run metrics from the open-source DeepEval and AutoEval
libraries. This script covers both ways to use them:

  1. MANAGED  (evaluatorType=ThirdParty)
       Reference an evaluator by ID, e.g. ThirdParty.DeepEval.TaskCompletion or
       ThirdParty.AutoEval.Security. The service picks the model, runs it on its
       own capacity, and manages the library version. A managed 3p ID works
       anywhere a Builtin.* ID does: on-demand, online, or batch.

  2. CUSTOM-DERIVED  (evaluatorType=CustomDerived)
       An evaluator that reuses a base evaluator's prompt and scoring (a built-in
       or a managed 3p evaluator) but runs on a Bedrock model you choose. The
       base owns the prompt and scale; you own the model.

Every evaluator is one (evaluatorType, provider) pair:

    Managed built-in       Builtin        AWS
    Managed third-party    ThirdParty     DeepEval | AutoEval
    Derived from built-in  CustomDerived  AWS
    Derived from 3p        CustomDerived  DeepEval | AutoEval

The parent llm-as-a-judge sample can only run its custom evaluators on-demand,
because they use reference-input placeholders ({expected_response}, {assertions})
that live traffic has no ground truth for. Many managed 3p metrics (Toxicity,
Bias, PIILeakage, AutoEval.Security) need no reference input, so step 5 puts two
of them on an online config.

Usage:
    python evaluate.py [--region REGION] [--config PATH]

Args:
    --region    AWS region (default: from agent_config.json or boto3 session)
    --config    Path to agent_config.json written by ../../utils/deploy.py

Prerequisites:
    1. Deploy the shared HR Assistant agent (runs once for all evaluate/ samples):
           cd ../../utils && python deploy.py [--region REGION]
    2. Install evaluation dependencies:
           pip install -r requirements.txt

Outputs:
    results/discovered_evaluators.json  - third-party evaluators in your account
    results/on_demand_results.json      - managed + derived + built-in scores
    results/online_eval_config.json     - online config using reference-free 3p metrics

Docs:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/third-party-evaluators.html
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3
from boto3.session import Session

# ============================================================
# 0. Parse args and load agent config
# ============================================================

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / ".." / ".." / "utils" / "agent_config.json"
_RESULTS_DIR = _SCRIPT_DIR / "results"
_RESULTS_DIR.mkdir(exist_ok=True)

parser = argparse.ArgumentParser(description="Third-party evaluators for the HR Assistant agent")
parser.add_argument("--region", default=None, help="AWS region")
parser.add_argument(
    "--config",
    default=str(_DEFAULT_CONFIG),
    help="Path to agent_config.json (written by deploy.py)",
)
args = parser.parse_args()

_config_path = Path(args.config)
if not _config_path.exists():
    print(f"ERROR: Agent config not found at {_config_path}")
    print("Run deploy.py first:  cd ../../utils && python deploy.py")
    sys.exit(1)

_cfg = json.loads(_config_path.read_text())
AGENT_ID = _cfg["agent_id"]
AGENT_ARN = _cfg["agent_arn"]
CW_LOG_GROUP = _cfg["cw_log_group"]
REGION = args.region or _cfg.get("region") or Session().region_name or "us-east-1"

ACCOUNT_ID = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]

# Derive OTel service name from agent ARN:
# ARN format: arn:aws:bedrock-agentcore:{region}:{account}:runtime/{id}
_runtime_id = AGENT_ARN.split("/")[-1]
_agent_runtime_name = _runtime_id.rsplit("-", 1)[0]
OTEL_SERVICE_NAME = f"{_agent_runtime_name}.DEFAULT"

print("=" * 60)
print("HR Assistant Agent — Third-Party Evaluators")
print("=" * 60)
print(f"  Region       : {REGION}")
print(f"  Agent ID     : {AGENT_ID}")
print(f"  Agent ARN    : {AGENT_ARN}")
print(f"  CW Log Group : {CW_LOG_GROUP}")
print(f"  OTel Service : {OTEL_SERVICE_NAME}")

agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)
_cp = boto3.client("bedrock-agentcore-control", region_name=REGION)
iam_client = boto3.client("iam")

# ============================================================
# 1. Discover available third-party evaluators
# ============================================================
#
# Third-party evaluators are returned by ListEvaluators alongside the built-in
# evaluators and any custom evaluators in your account. We filter to
# evaluatorType == "ThirdParty" and capture each one's level (TRACE / SESSION),
# which we reuse below to prime the EvaluationClient level cache — the SDK
# cannot resolve the level of global (AWS-managed) evaluators via GetEvaluator.

print("\n[1/5] Discovering third-party evaluators (ListEvaluators) ...")

_third_party = []
_next_token = None
while True:
    _kwargs = {"maxResults": 100}
    if _next_token:
        _kwargs["nextToken"] = _next_token
    _page = _cp.list_evaluators(**_kwargs)
    for _e in _page.get("evaluators", []):
        if _e.get("evaluatorType") == "ThirdParty":
            _third_party.append(_e)
    _next_token = _page.get("nextToken")
    if not _next_token:
        break

# id -> level, used to prime the EvaluationClient cache
EVALUATOR_LEVELS = {_e["evaluatorId"]: _e.get("level", "TRACE") for _e in _third_party}

if _third_party:
    print(f"  Found {len(_third_party)} third-party evaluator(s):\n")
    print(f"  {'Evaluator ID':<40} {'Provider':<12} {'Level':<8} {'Status'}")
    print("  " + "-" * 74)
    for _e in sorted(_third_party, key=lambda x: (x.get("provider", ""), x["evaluatorId"])):
        print(f"  {_e['evaluatorId']:<40} {_e.get('provider', ''):<12} {_e.get('level', ''):<8} {_e.get('status', '')}")
else:
    print("  No third-party evaluators returned. They may not be available in")
    print(f"  region {REGION} yet, or your account may lack access.")

(_RESULTS_DIR / "discovered_evaluators.json").write_text(json.dumps(_third_party, indent=2, default=str))

# The managed third-party evaluators this sample uses. Pick reference-free
# metrics so they can run without ground truth (and online, later).
MANAGED_TASK_COMPLETION = "ThirdParty.DeepEval.TaskCompletion"  # goal accomplished?
MANAGED_TOXICITY = "ThirdParty.DeepEval.Toxicity"  # attacks / hate / threats?
MANAGED_SECURITY = "ThirdParty.AutoEval.Security"  # response malicious?

# ============================================================
# 2. Create a custom evaluator derived from a third-party base
# ============================================================
#
# A derived evaluator reuses a base evaluator's prompt and scoring but runs on a
# model you choose. Don't set instructions / ratingScale: the base owns both.
# The result has evaluatorType=CustomDerived and inherits the base evaluator's
# provider (DeepEval here).
#
# `level` must match the base evaluator's level. The API requires it even though
# it's derived, so we pass the level discovered for the base in step 1.
#
# The model runs in your account with your credentials (the caller's for
# on-demand, the execution role for online), rather than on service capacity as
# a managed evaluator does.

print("\n[2/5] Creating a custom evaluator derived from a 3p base ...")

_SUFFIX = uuid.uuid4().hex[:8]
_BASE_LEVEL = EVALUATOR_LEVELS.get(MANAGED_TASK_COMPLETION, "TRACE")

_derived = _cp.create_evaluator(
    evaluatorName=f"MyTaskCompletion_{_SUFFIX}",
    level=_BASE_LEVEL,
    evaluatorConfig={
        "derived": {
            "baseEvaluatorId": MANAGED_TASK_COMPLETION,
            "modelConfig": {
                "bedrockEvaluatorModelConfig": {
                    # Any Bedrock model works here.
                    "modelId": "us.amazon.nova-lite-v1:0",
                    "inferenceConfig": {
                        "temperature": 0.0,
                        "topP": 1.0,
                        "maxTokens": 2048,
                    },
                }
            },
        }
    },
)
DERIVED_EVALUATOR_ID = _derived["evaluatorId"]
EVALUATOR_LEVELS[DERIVED_EVALUATOR_ID] = _BASE_LEVEL
print(f"  Created MyTaskCompletion (derived from {MANAGED_TASK_COMPLETION})")
print(f"    evaluatorId: {DERIVED_EVALUATOR_ID}")
print("    runs on:     us.amazon.nova-lite-v1:0")

# ============================================================
# 3. Invoke agent to generate a session
# ============================================================
#
# The HR assistant agent is already deployed (../../utils/deploy.py). We invoke
# it for a multi-turn session so there are CloudWatch spans to evaluate. A unique
# runtimeSessionId groups all turns together.

print("\n[3/5] Invoking HR Assistant to generate a session ...")

SESSION_ID = f"3p-eval-{uuid.uuid4()}"
print(f"  Session ID: {SESSION_ID}")

TURNS = [
    "What is the PTO balance for employee EMP-001?",
    "Please submit a PTO request for EMP-001 from 2026-07-14 to 2026-07-18.",
    "What is the company remote work policy?",
]


def _invoke_turn(prompt: str) -> str:
    resp = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        qualifier="DEFAULT",
        runtimeSessionId=SESSION_ID,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
    )
    raw = resp["response"].read().decode("utf-8")
    parts = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            chunk = line[len("data: ") :]
            try:
                parts.append(str(json.loads(chunk)))
            except json.JSONDecodeError:
                parts.append(chunk)
    return "".join(parts) if parts else raw


for i, prompt in enumerate(TURNS, 1):
    print(f"  Turn {i}: {prompt[:70]}")
    reply = _invoke_turn(prompt)
    print(f"         -> {reply[:100]}")

# On-demand evaluation reads span documents from the aws/spans log group, which
# is populated by CloudWatch Transaction Search (see the prerequisite in the
# README). Indexing lags span emission, so wait before evaluating — allow extra
# time right after first enabling Transaction Search.
print("\n  Waiting 180s for CloudWatch span ingestion + indexing ...")
time.sleep(180)
print("  Ready for evaluation.")

# ============================================================
# 4. On-demand evaluation with EvaluationClient
# ============================================================
#
# Pass managed 3p, derived, and built-in evaluator IDs together in one call. The
# metrics here are all reference-free, so no ReferenceInputs are needed.

from datetime import timedelta

from bedrock_agentcore.evaluation import EvaluationClient

print("\n[4/5] Running on-demand evaluation (EvaluationClient) ...")

ec = EvaluationClient(region_name=REGION)

EVALUATOR_IDS = [
    MANAGED_TASK_COMPLETION,  # managed 3p: did the agent accomplish the goal?
    MANAGED_TOXICITY,  # managed 3p: any toxic content?
    MANAGED_SECURITY,  # managed 3p: is the response malicious?
    DERIVED_EVALUATOR_ID,  # derived:    TaskCompletion on our own Nova model
    "Builtin.Helpfulness",  # built-in:   for side-by-side comparison
]

# Prime the evaluator level cache — the SDK cannot resolve the level of global
# (AWS-managed) evaluators via GetEvaluator. We take the third-party levels from
# ListEvaluators (step 1) and add the one built-in we use.
_level_cache = {eid: EVALUATOR_LEVELS[eid] for eid in EVALUATOR_IDS if eid in EVALUATOR_LEVELS}
_level_cache["Builtin.Helpfulness"] = "TRACE"
ec._evaluator_level_cache.update(_level_cache)

on_demand_results = ec.run(
    evaluator_ids=EVALUATOR_IDS,
    agent_id=AGENT_ID,
    session_id=SESSION_ID,
    look_back_time=timedelta(hours=1),
)

print(f"\n  Received {len(on_demand_results)} result(s):\n")
print(f"  {'Evaluator':<42} {'Value':<8} {'Label'}")
print("  " + "-" * 74)
for result in on_demand_results:
    evaluator_id = result.get("evaluatorId", "")
    name = "MyTaskCompletion (derived)" if evaluator_id == DERIVED_EVALUATOR_ID else evaluator_id
    value = result.get("value", result.get("score", "N/A"))
    label = result.get("label", result.get("rating", "N/A"))
    error = result.get("errorCode")
    if error:
        label = f"ERR:{error}"
    print(f"  {name:<42} {value!s:<8} {label!s}")

_results_path = _RESULTS_DIR / "on_demand_results.json"
_results_path.write_text(
    json.dumps(
        {
            "session_id": SESSION_ID,
            "evaluators": EVALUATOR_IDS,
            "derived_evaluator_id": DERIVED_EVALUATOR_ID,
            "results": on_demand_results,
        },
        indent=2,
        default=str,
    )
)
print(f"\n  Results saved: {_results_path}")

# ============================================================
# 5. Online evaluation with reference-free managed 3p metrics
# ============================================================
#
# These managed 3p metrics are reference-free, so they can score live traffic.
# The parent sample's custom evaluators need reference inputs, which is why they
# stay on-demand only.

print("\n[5/5] Creating online evaluation configuration ...")

# ---- 5a. IAM role for the evaluation service -------------------------
ONLINE_EVAL_ROLE_NAME = "AgentCoreOnlineEvaluationRole"
ONLINE_EVAL_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/{ONLINE_EVAL_ROLE_NAME}"

_trust_policy = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
)

_inline_policy = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "CloudWatchLogsReadWrite",
                "Effect": "Allow",
                "Action": [
                    "logs:FilterLogEvents",
                    "logs:GetLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                    "logs:StartQuery",
                    "logs:GetQueryResults",
                    "logs:StopQuery",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "*",
            },
            {
                "Sid": "BedrockInvokeForJudge",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
            },
        ],
    }
)

try:
    iam_client.get_role(RoleName=ONLINE_EVAL_ROLE_NAME)
    iam_client.put_role_policy(
        RoleName=ONLINE_EVAL_ROLE_NAME,
        PolicyName="AgentCoreOnlineEvalPolicy",
        PolicyDocument=_inline_policy,
    )
    print(f"  Using existing IAM role: {ONLINE_EVAL_ROLE_ARN}")
except iam_client.exceptions.NoSuchEntityException:
    iam_client.create_role(
        RoleName=ONLINE_EVAL_ROLE_NAME,
        AssumeRolePolicyDocument=_trust_policy,
        Description="Execution role for AgentCore online evaluation",
    )
    iam_client.put_role_policy(
        RoleName=ONLINE_EVAL_ROLE_NAME,
        PolicyName="AgentCoreOnlineEvalPolicy",
        PolicyDocument=_inline_policy,
    )
    print(f"  Created IAM role: {ONLINE_EVAL_ROLE_ARN}")

print("  Waiting 10s for IAM propagation ...")
time.sleep(10)

# ---- 5b. Create online evaluation config ----------------------------
# Config name: alphanumeric + underscores only (no hyphens).
ONLINE_EVAL_CONFIG_NAME = f"hr_3p_eval_{_SUFFIX}"

# Reference-free managed 3p metrics, safe for live traffic. They obey the same
# sampling and filtering rules as built-in and custom evaluators.
_ONLINE_EVALUATORS = [
    MANAGED_TOXICITY,
    MANAGED_SECURITY,
]

print(f"  Config name  : {ONLINE_EVAL_CONFIG_NAME}")
print(f"  Log group    : {CW_LOG_GROUP}")
print(f"  OTel service : {OTEL_SERVICE_NAME}")
print(f"  Evaluators   : {', '.join(_ONLINE_EVALUATORS)}")

_online_resp = _cp.create_online_evaluation_config(
    onlineEvaluationConfigName=ONLINE_EVAL_CONFIG_NAME,
    # 100% sampling in this example; lower for high-traffic production agents.
    rule={"samplingConfig": {"samplingPercentage": 100.0}},
    dataSourceConfig={
        "cloudWatchLogs": {
            "logGroupNames": [CW_LOG_GROUP],
            "serviceNames": [OTEL_SERVICE_NAME],
        }
    },
    evaluators=[{"evaluatorId": eid} for eid in _ONLINE_EVALUATORS],
    evaluationExecutionRoleArn=ONLINE_EVAL_ROLE_ARN,
    enableOnCreate=True,
)

ONLINE_CONFIG_ID = _online_resp["onlineEvaluationConfigId"]
ONLINE_CONFIG_ARN = _online_resp.get("onlineEvaluationConfigArn", "")

print("\n  Online evaluation config created:")
print(f"    ID  : {ONLINE_CONFIG_ID}")
print(f"    ARN : {ONLINE_CONFIG_ARN}")
print()
print("  The config is now ACTIVE. Every new HR assistant session is scored")
print("  automatically with the reference-free third-party metrics above.")
print("  Results appear in CloudWatch at:")
print(f"    /aws/bedrock-agentcore/evaluations/results/{ONLINE_CONFIG_ID}")

_online_path = _RESULTS_DIR / "online_eval_config.json"
_online_path.write_text(
    json.dumps(
        {
            "config_name": ONLINE_EVAL_CONFIG_NAME,
            "config_id": ONLINE_CONFIG_ID,
            "config_arn": ONLINE_CONFIG_ARN,
            "online_evaluators": _ONLINE_EVALUATORS,
            "results_log_group": f"/aws/bedrock-agentcore/evaluations/results/{ONLINE_CONFIG_ID}",
        },
        indent=2,
    )
)
print(f"\n  Config details saved: {_online_path}")

# ============================================================
# Summary
# ============================================================

print("\n" + "=" * 60)
print("Summary")
print("=" * 60)
print(f"  Third-party evaluators found : {len(_third_party)}")
print(f"  Derived evaluator created    : MyTaskCompletion ({DERIVED_EVALUATOR_ID})")
print(f"  On-demand evaluation         : {len(on_demand_results)} result(s) for session {SESSION_ID[:20]}...")
print(f"  Online eval config           : {ONLINE_EVAL_CONFIG_NAME} (ENABLED)")
print()
print("  Next steps:")
print("  - Check on-demand scores: results/on_demand_results.json")
print("  - Monitor online eval: AWS Console → CloudWatch → Log groups")
print(f"    /aws/bedrock-agentcore/evaluations/results/{ONLINE_CONFIG_ID}")
print("  - Disable online config when done:")
print("    aws bedrock-agentcore-control update-online-evaluation-config \\")
print(f"        --online-evaluation-config-id {ONLINE_CONFIG_ID} \\")
print("        --enable-config false")
