"""
Evaluate the Claude Agent SDK HR Assistant with AgentCore Evaluations.

Demonstrates:
  1. On-Demand Evaluation — invoke agent, then evaluate the recorded spans
  2. Online Evaluation — create a persistent config for continuous monitoring

Usage:
    python evaluate.py [--region REGION] [--config PATH]

Prerequisites:
    1. Deploy the agent: python deploy.py
    2. pip install -r requirements.txt
"""

import argparse
import json
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import boto3
from bedrock_agentcore.evaluation import EvaluationClient
from bedrock_agentcore.evaluation.client import ReferenceInputs

# Add shared module to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.mock_data import ASSERTIONS, EVAL_TURNS, EXPECTED_RESPONSES, EXPECTED_TRAJECTORY

# ============================================================
# 0. Parse args and load agent config
# ============================================================

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / "agent_config.json"
_RESULTS_DIR = _SCRIPT_DIR / "results"
_RESULTS_DIR.mkdir(exist_ok=True)

parser = argparse.ArgumentParser(description="Evaluate Claude Agent SDK HR Assistant")
parser.add_argument("--region", default=None, help="AWS region")
parser.add_argument("--config", default=str(_DEFAULT_CONFIG), help="Path to agent_config.json")
args = parser.parse_args()

_config_path = Path(args.config)
if not _config_path.exists():
    print(f"ERROR: Agent config not found at {_config_path}")
    print("Run deploy.py first: python deploy.py")
    sys.exit(1)

_cfg = json.loads(_config_path.read_text())
AGENT_ID = _cfg["agent_id"]
AGENT_ARN = _cfg["agent_arn"]
CW_LOG_GROUP = _cfg["cw_log_group"]
REGION = args.region or _cfg.get("region", "us-east-1")

ACCOUNT_ID = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]

_runtime_id = AGENT_ARN.split("/")[-1]
_agent_runtime_name = _runtime_id.rsplit("-", 1)[0]
OTEL_SERVICE_NAME = f"{_agent_runtime_name}.DEFAULT"

print("=" * 60)
print("Claude Agent SDK — AgentCore Evaluation")
print("=" * 60)
print(f"  Region       : {REGION}")
print(f"  Agent ID     : {AGENT_ID}")
print(f"  Agent ARN    : {AGENT_ARN}")
print(f"  CW Log Group : {CW_LOG_GROUP}")
print(f"  OTel Service : {OTEL_SERVICE_NAME}")

agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)
cp_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
iam_client = boto3.client("iam")

# ============================================================
# 1. Create custom LLM-as-a-judge evaluator
# ============================================================

print("\n[1/4] Creating custom LLM-as-a-judge evaluator ...")

_SUFFIX = uuid.uuid4().hex[:8]

print("  Creating HRResponseQuality (TRACE) ...")
_resp_quality = cp_client.create_evaluator(
    evaluatorName=f"HRResponseQuality_claude_sdk_{_SUFFIX}",
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
                    {"value": 0.0, "label": "poor", "definition": "Inaccurate, incomplete, or unprofessional."},
                    {"value": 0.5, "label": "acceptable", "definition": "Mostly correct but missing details."},
                    {"value": 1.0, "label": "excellent", "definition": "Accurate, complete, and professional."},
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
CUSTOM_EVALUATOR_ID = _resp_quality["evaluatorId"]
print(f"    evaluatorId: {CUSTOM_EVALUATOR_ID}")

# ============================================================
# 2. Invoke agent to generate a session
# ============================================================

print("\n[2/4] Invoking Claude Agent SDK HR Assistant ...")

SESSION_ID = f"claude-sdk-eval-{uuid.uuid4()}"
print(f"  Session ID: {SESSION_ID}")


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


for i, prompt in enumerate(EVAL_TURNS, 1):
    print(f"  Turn {i}: {prompt[:70]}")
    reply = _invoke_turn(prompt)
    print(f"         -> {reply[:100]}")

print("\n  Waiting 90s for CloudWatch span ingestion ...")
time.sleep(90)
print("  Ready for evaluation.")

# ============================================================
# 3. On-Demand Evaluation
# ============================================================

print("\n[3/4] Running on-demand evaluation (EvaluationClient) ...")

ec = EvaluationClient(region_name=REGION)

ec._evaluator_level_cache.update(
    {
        "Builtin.GoalSuccessRate": "SESSION",
        "Builtin.Correctness": "TRACE",
        "Builtin.Helpfulness": "TRACE",
        CUSTOM_EVALUATOR_ID: "TRACE",
    }
)

EVALUATOR_IDS = [
    "Builtin.GoalSuccessRate",
    "Builtin.Correctness",
    "Builtin.Helpfulness",
    CUSTOM_EVALUATOR_ID,
]

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

print(f"\n  Received {len(on_demand_results)} result(s):\n")
print(f"  {'Evaluator':<40} {'Value':<8} {'Label'}")
print("  " + "-" * 65)

for result in on_demand_results:
    evaluator_id = result.get("evaluatorId", "")
    name = evaluator_id if evaluator_id.startswith("Builtin.") else "HRResponseQuality"
    value = result.get("value", result.get("score", "N/A"))
    label = result.get("label", result.get("rating", "N/A"))
    error = result.get("errorCode")
    if error:
        label = f"ERR:{error}"
    print(f"  {name:<40} {str(value):<8} {str(label)}")

_results_path = _RESULTS_DIR / "on_demand_results.json"
_results_path.write_text(
    json.dumps(
        {
            "framework": "claude-agent-sdk",
            "session_id": SESSION_ID,
            "evaluators": EVALUATOR_IDS,
            "custom_evaluator_id": CUSTOM_EVALUATOR_ID,
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

print("\n[4/4] Creating online evaluation configuration ...")

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
                "Sid": "CloudWatchLogs",
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
                "Sid": "BedrockJudge",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    f"arn:aws:bedrock:{REGION}::foundation-model/us.amazon.nova-lite-v1:0",
                ],
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

ONLINE_CONFIG_NAME = f"hr_claude_sdk_eval_{_SUFFIX}"
_ONLINE_EVALUATORS = ["Builtin.GoalSuccessRate", "Builtin.Correctness", "Builtin.Helpfulness"]

_online_resp = cp_client.create_online_evaluation_config(
    onlineEvaluationConfigName=ONLINE_CONFIG_NAME,
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
print(f"\n  Online config created: {ONLINE_CONFIG_NAME}")
print(f"  Config ID: {ONLINE_CONFIG_ID}")
print("  Status: ENABLED (scoring all future sessions)")

_online_path = _RESULTS_DIR / "online_eval_config.json"
_online_path.write_text(
    json.dumps(
        {
            "framework": "claude-agent-sdk",
            "config_name": ONLINE_CONFIG_NAME,
            "config_id": ONLINE_CONFIG_ID,
            "custom_evaluator_id": CUSTOM_EVALUATOR_ID,
            "results_log_group": f"/aws/bedrock-agentcore/evaluations/results/{ONLINE_CONFIG_ID}",
        },
        indent=2,
    )
)

# ============================================================
# Summary
# ============================================================

print("\n" + "=" * 60)
print("Evaluation Complete — Claude Agent SDK")
print("=" * 60)
print(f"  On-demand results : results/on_demand_results.json ({len(on_demand_results)} scores)")
print(f"  Online config     : {ONLINE_CONFIG_NAME} (ENABLED)")
print(f"  Custom evaluator  : {CUSTOM_EVALUATOR_ID}")
print()
print("  Next steps:")
print("  - Review scores in results/on_demand_results.json")
print("  - Monitor online eval in CloudWatch:")
print(f"    /aws/bedrock-agentcore/evaluations/results/{ONLINE_CONFIG_ID}")
print("  - When done: python cleanup.py")
