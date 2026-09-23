"""
LLM-as-a-Judge Evaluation of the HR Assistant Agent.

Demonstrates two ways to evaluate your agent using LLM-as-a-judge:

  1. On-Demand Evaluation (EvaluationClient)
       Invoke the agent for a session, then evaluate the recorded CloudWatch
       spans immediately. Built-in evaluators + custom LLM-as-a-judge evaluators
       run in the same call. Use for spot-checks and CI/CD regression tests.

  2. Online Evaluation (create_online_evaluation_config)
       Create a persistent config that continuously monitors your agent's live
       traffic and scores every sampled session automatically. Results flow into
       CloudWatch; no per-session API call is required.

Custom LLM-as-a-judge evaluators let you express domain-specific quality
criteria in natural language. The service substitutes ground-truth placeholders
at evaluation time:

  TRACE-level (one score per agent turn):
    {assistant_turn}    - the agent's actual response for that turn
    {expected_response} - expectedResponse from ReferenceInputs
    {context}           - conversation context before this turn

  SESSION-level (one score per complete session):
    {actual_tool_trajectory}   - tools the agent actually called
    {expected_tool_trajectory} - expectedTrajectory from ReferenceInputs
    {assertions}               - assertions list from ReferenceInputs
    {available_tools}          - tools declared in agent definition

Usage:
    python evaluate.py [--region REGION] [--config PATH]

Args:
    --region    AWS region (default: from agent_config.json or boto3 session)
    --config    Path to agent_config.json written by deploy.py
                (default: ../utils/agent_config.json)

Prerequisites:
    1. Deploy the HR Assistant agent:
           cd ../utils && python deploy.py [--region REGION]
    2. Install evaluation dependencies:
           pip install -r requirements.txt

Outputs:
    results/on_demand_results.json    - EvaluationClient scores
    results/online_eval_config.json   - Online evaluation config details
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
_DEFAULT_CONFIG = _SCRIPT_DIR / ".." / "utils" / "agent_config.json"
_RESULTS_DIR = _SCRIPT_DIR / "results"
_RESULTS_DIR.mkdir(exist_ok=True)

parser = argparse.ArgumentParser(description="LLM-as-a-judge evaluation for the HR Assistant agent")
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
    print("Run deploy.py first:  cd ../utils && python deploy.py")
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
print("HR Assistant Agent — LLM-as-a-Judge Evaluation")
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
# 1. Create custom LLM-as-a-judge evaluators
# ============================================================
#
# Custom evaluators define quality criteria in natural language.
# The service substitutes ground-truth placeholders at evaluation time.
#
# Two evaluator types useful for HR assistants:
#   - TRACE-level: score each agent response against the expected answer
#   - SESSION-level: check whether the right tools were called and all
#     assertions are satisfied across the whole conversation

print("\n[1/4] Creating custom LLM-as-a-judge evaluators ...")

_SUFFIX = uuid.uuid4().hex[:8]

# ---- Trace-level: HR response quality --------------------------------
# Scores how well the agent's response matches the expected HR answer.
# Evaluates accuracy of specific numbers (PTO days, dollar amounts, etc.),
# completeness, and professional tone.
print("  Creating HRResponseQuality (TRACE) ...")
_resp_quality = _cp.create_evaluator(
    evaluatorName=f"HRResponseQuality_{_SUFFIX}",
    level="TRACE",
    evaluatorConfig={
        "llmAsAJudge": {
            "instructions": (
                "You are evaluating an HR assistant chatbot response.\n\n"
                "Agent response: {assistant_turn}\n"
                "Expected response: {expected_response}\n\n"
                "Rate the quality of the agent's response on the following criteria:\n"
                "1. ACCURACY: Key facts (numbers, dates, names) match the expected response\n"
                "2. COMPLETENESS: All important information is present\n"
                "3. PROFESSIONALISM: Tone is appropriate for an HR context\n\n"
                "If no expected_response is provided, evaluate based on accuracy and helpfulness alone.\n"
                "Assign a single overall quality rating."
            ),
            "ratingScale": {
                "numerical": [
                    {
                        "value": 0.0,
                        "label": "poor",
                        "definition": "Response is inaccurate, incomplete, or unprofessional.",
                    },
                    {
                        "value": 0.5,
                        "label": "acceptable",
                        "definition": "Response is mostly correct but missing details or slightly off.",
                    },
                    {
                        "value": 1.0,
                        "label": "excellent",
                        "definition": "Response is accurate, complete, and professionally written.",
                    },
                ]
            },
            "modelConfig": {
                "bedrockEvaluatorModelConfig": {
                    "modelId": "us.amazon.nova-lite-v1:0",
                    "inferenceConfig": {"maxTokens": 512},
                }
            },
        }
    },
)
CUSTOM_RESPONSE_QUALITY_ID = _resp_quality["evaluatorId"]
print(f"    evaluatorId: {CUSTOM_RESPONSE_QUALITY_ID}")

# ---- Session-level: HR session completeness --------------------------
# Checks whether the agent fulfilled the user's request across the whole
# conversation — right tools called, correct outcome, all assertions met.
print("  Creating HRSessionCompleteness (SESSION) ...")
_session_check = _cp.create_evaluator(
    evaluatorName=f"HRSessionCompleteness_{_SUFFIX}",
    level="SESSION",
    evaluatorConfig={
        "llmAsAJudge": {
            "instructions": (
                "You are reviewing a complete HR assistant conversation.\n\n"
                "Expected tool trajectory: {expected_tool_trajectory}\n"
                "Actual tool trajectory:   {actual_tool_trajectory}\n"
                "Session assertions: {assertions}\n\n"
                "Evaluate whether the agent:\n"
                "1. Called the expected tools (in any order)\n"
                "2. Satisfied all session assertions\n"
                "3. Reached a successful resolution for the user's request\n\n"
                "Rate the overall session completeness."
            ),
            "ratingScale": {
                "numerical": [
                    {
                        "value": 0.0,
                        "label": "incomplete",
                        "definition": "Agent failed to call required tools or left the request unresolved.",
                    },
                    {
                        "value": 0.5,
                        "label": "partial",
                        "definition": "Agent partially fulfilled the request — some tools missing or assertions unmet.",
                    },
                    {
                        "value": 1.0,
                        "label": "complete",
                        "definition": "Agent called all expected tools and satisfied every assertion.",
                    },
                ]
            },
            "modelConfig": {
                "bedrockEvaluatorModelConfig": {
                    "modelId": "us.amazon.nova-lite-v1:0",
                    "inferenceConfig": {"maxTokens": 512},
                }
            },
        }
    },
)
CUSTOM_SESSION_COMPLETENESS_ID = _session_check["evaluatorId"]
print(f"    evaluatorId: {CUSTOM_SESSION_COMPLETENESS_ID}")

# ============================================================
# 2. Invoke agent to generate a session
# ============================================================
#
# The HR assistant agent is already deployed (shared/deploy.py).
# We invoke it for a multi-turn session so there are CloudWatch spans
# to evaluate. A unique runtimeSessionId groups all turns together.

print("\n[2/4] Invoking HR Assistant to generate a session ...")

SESSION_ID = f"llm-judge-eval-{uuid.uuid4()}"
print(f"  Session ID: {SESSION_ID}")

TURNS = [
    "What is the PTO balance for employee EMP-001?",
    "Please submit a PTO request for EMP-001 from 2026-07-14 to 2026-07-18.",
    "What is the company remote work policy?",
]

EXPECTED_RESPONSES = [
    "Employee EMP-001 has 10 remaining PTO days out of 15 total (5 days used).",
    "PTO request submitted for EMP-001 from 2026-07-14 to 2026-07-18. Request ID: PTO-2026-NNN.",
    "The company allows up to 3 days of remote work per week. Core hours are 10am–3pm.",
]

EXPECTED_TRAJECTORY = ["get_pto_balance", "submit_pto_request", "lookup_hr_policy"]

ASSERTIONS = [
    "Agent called get_pto_balance with employee_id=EMP-001",
    "Agent reported 10 remaining PTO days",
    "Agent submitted a PTO request and returned a request ID",
    "Agent described the remote work policy",
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
                chunk = json.loads(chunk)
            except Exception:
                pass
            parts.append(str(chunk))
    return "".join(parts) if parts else raw


for i, (prompt, expected) in enumerate(zip(TURNS, EXPECTED_RESPONSES), 1):
    print(f"  Turn {i}: {prompt[:70]}")
    reply = _invoke_turn(prompt)
    print(f"         -> {reply[:100]}")

print("\n  Waiting 90s for CloudWatch span ingestion ...")
time.sleep(90)
print("  Ready for evaluation.")

# ============================================================
# 3. On-Demand Evaluation with EvaluationClient
# ============================================================
#
# EvaluationClient evaluates the recorded session spans from CloudWatch.
# You can mix built-in evaluators with your custom LLM-as-a-judge evaluators
# in the same call. Provide ReferenceInputs ground truth to unlock evaluators
# that require expected responses or trajectories.

from bedrock_agentcore.evaluation import EvaluationClient  # noqa: E402
from bedrock_agentcore.evaluation.client import ReferenceInputs  # noqa: E402
from datetime import timedelta  # noqa: E402

print("\n[3/4] Running on-demand evaluation (EvaluationClient) ...")

ec = EvaluationClient(region_name=REGION)

# Pre-populate the evaluator level cache — required for Builtin.* evaluators
# because the SDK cannot resolve their level via GetEvaluator API.
ec._evaluator_level_cache.update(
    {
        "Builtin.GoalSuccessRate": "SESSION",
        "Builtin.Correctness": "TRACE",
        "Builtin.Helpfulness": "TRACE",
        CUSTOM_RESPONSE_QUALITY_ID: "TRACE",
        CUSTOM_SESSION_COMPLETENESS_ID: "SESSION",
    }
)

EVALUATOR_IDS = [
    "Builtin.GoalSuccessRate",  # SESSION: did the agent meet the user's goal?
    "Builtin.Correctness",  # TRACE: is each response factually correct?
    "Builtin.Helpfulness",  # TRACE: was each response helpful?
    CUSTOM_RESPONSE_QUALITY_ID,  # TRACE: HR-specific response quality
    CUSTOM_SESSION_COMPLETENESS_ID,  # SESSION: did all assertions pass?
]

# ReferenceInputs provide ground truth for evaluators that need it.
# assertions are checked by session-level evaluators (GoalSuccessRate, HRSessionCompleteness).
# expected_trajectory lists the tools the agent should have called.
# expected_response (plain string) is used by TRACE-level evaluators for the last turn.
REFERENCE_INPUTS = ReferenceInputs(
    assertions=ASSERTIONS,
    expected_trajectory=EXPECTED_TRAJECTORY,
    expected_response=EXPECTED_RESPONSES[-1],
)

on_demand_results = ec.run(
    evaluator_ids=EVALUATOR_IDS,
    agent_id=AGENT_ID,
    session_id=SESSION_ID,
    look_back_time=timedelta(hours=1),
    reference_inputs=REFERENCE_INPUTS,
)

# Display results
print(f"\n  Received {len(on_demand_results)} result(s):\n")
print(f"  {'Evaluator':<45} {'Level':<8} {'Value':<8} {'Label'}")
print("  " + "-" * 80)

for result in on_demand_results:
    evaluator_id = result.get("evaluatorId", "")
    name = (
        evaluator_id
        if evaluator_id.startswith("Builtin.")
        else ("HRResponseQuality" if evaluator_id == CUSTOM_RESPONSE_QUALITY_ID else "HRSessionCompleteness")
    )
    value = result.get("value", result.get("score", "N/A"))
    label = result.get("label", result.get("rating", "N/A"))
    error = result.get("errorCode")
    if error:
        label = f"ERR:{error}"
    print(f"  {name:<45} {str(value):<8} {str(label)}")

# Save results
_results_path = _RESULTS_DIR / "on_demand_results.json"
_results_path.write_text(
    json.dumps(
        {
            "session_id": SESSION_ID,
            "evaluators": EVALUATOR_IDS,
            "custom_evaluator_ids": {
                "HRResponseQuality": CUSTOM_RESPONSE_QUALITY_ID,
                "HRSessionCompleteness": CUSTOM_SESSION_COMPLETENESS_ID,
            },
            "results": on_demand_results,
        },
        indent=2,
        default=str,
    )
)
print(f"\n  Results saved: {_results_path}")

# ============================================================
# 4. Online Evaluation Configuration
# ============================================================
#
# Online evaluation monitors live agent traffic continuously.
# Create a config once; it evaluates every sampled session automatically.
# You can use any mix of built-in and custom LLM-as-a-judge evaluators.
#
# Note: Once a config is ENABLED, its evaluators are LOCKED.
# To update an evaluator: disable the config → update → re-enable.

print("\n[4/4] Creating online evaluation configuration ...")

# ---- 4a. IAM role for the evaluation service -------------------------
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
        Description="Execution role for AgentCore online LLM-as-a-judge evaluation",
    )
    iam_client.put_role_policy(
        RoleName=ONLINE_EVAL_ROLE_NAME,
        PolicyName="AgentCoreOnlineEvalPolicy",
        PolicyDocument=_inline_policy,
    )
    print(f"  Created IAM role: {ONLINE_EVAL_ROLE_ARN}")

print("  Waiting 10s for IAM propagation ...")
time.sleep(10)

# ---- 4b. Create online evaluation config ----------------------------
# Config name: alphanumeric + underscores only (no hyphens)
ONLINE_EVAL_CONFIG_NAME = f"hr_llm_judge_eval_{_SUFFIX}"

# Note: Custom evaluators that use reference input placeholders
# ({expected_response}, {assertions}, etc.) require ground truth and therefore
# can only be used in on-demand evaluation. Online evaluation evaluates live
# traffic where no ground truth is available, so only built-in evaluators
# (or custom evaluators without reference inputs) are supported here.
_ONLINE_EVALUATORS = [
    "Builtin.GoalSuccessRate",
    "Builtin.Correctness",
    "Builtin.Helpfulness",
]

print(f"  Config name  : {ONLINE_EVAL_CONFIG_NAME}")
print(f"  Log group    : {CW_LOG_GROUP}")
print(f"  OTel service : {OTEL_SERVICE_NAME}")
print(f"  Evaluators   : {', '.join(_ONLINE_EVALUATORS)}")
print("  Note: Custom evaluators with reference inputs are on-demand only")

_online_resp = _cp.create_online_evaluation_config(
    onlineEvaluationConfigName=ONLINE_EVAL_CONFIG_NAME,
    # 100% sampling in this example; lower for high-traffic production agents
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
print("  The config is now ACTIVE. Every new HR assistant session will be")
print("  automatically evaluated with built-in evaluators.")
print("  (Custom evaluators with reference inputs are on-demand only.)")
print("  Results appear in CloudWatch at:")
print(f"    /aws/bedrock-agentcore/evaluations/results/{ONLINE_CONFIG_ID}")

# ---- 4c. Invoke agent to trigger online evaluation ------------------
print("\n  Invoking agent to trigger a live online evaluation ...")

_online_session = f"online-judge-{uuid.uuid4()}"
_online_prompts = [
    "What is the PTO balance for employee EMP-042?",
    "What health insurance options does the company offer?",
]

for prompt in _online_prompts:
    print(f"    > {prompt[:70]}")
    reply = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        qualifier="DEFAULT",
        runtimeSessionId=_online_session,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
    )
    reply.get("response", b"").read()  # consume stream

print("  Online evaluation will score this session automatically.")
print("  Results appear in CloudWatch within a few minutes.")

# Save online eval config details
_online_path = _RESULTS_DIR / "online_eval_config.json"
_online_path.write_text(
    json.dumps(
        {
            "config_name": ONLINE_EVAL_CONFIG_NAME,
            "config_id": ONLINE_CONFIG_ID,
            "config_arn": ONLINE_CONFIG_ARN,
            "custom_evaluator_ids": {
                "HRResponseQuality": CUSTOM_RESPONSE_QUALITY_ID,
                "HRSessionCompleteness": CUSTOM_SESSION_COMPLETENESS_ID,
            },
            "triggered_session_id": _online_session,
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
print("  Custom evaluators created : HRResponseQuality, HRSessionCompleteness")
print(f"  On-demand evaluation      : {len(on_demand_results)} result(s) for session {SESSION_ID[:20]}...")
print(f"  Online eval config        : {ONLINE_EVAL_CONFIG_NAME} (ENABLED)")
print()
print("  Next steps:")
print("  - Check on-demand scores: results/on_demand_results.json")
print("  - Monitor online eval: AWS Console → CloudWatch → Log groups")
print(f"    /aws/bedrock-agentcore/evaluations/results/{ONLINE_CONFIG_ID}")
print("  - Disable online config when done:")
print("    aws bedrock-agentcore-control update-online-evaluation-config \\")
print(f"        --online-evaluation-config-id {ONLINE_CONFIG_ID} \\")
print("        --enable-config false")
