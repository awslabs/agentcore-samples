"""Deploy the 5 Market Trends code-based evaluators end-to-end.

What this does, in order:
  1. Create / update the evaluation execution IAM role (trust + inline perms).
  2. Package each Lambda under evaluators/<name>/ as a zip, create or update
     the function with a per-evaluator least-privilege execution role.
  3. Register each Lambda as an AgentCore evaluator (Control Plane).
  4. Create an online evaluation config that points the evaluators at the
     deployed Market Trends Agent's runtime log group.

All resources are idempotent: re-running the script updates rather than
creates duplicates. Re-runs skip already-active evaluators (evaluator
definitions are immutable after creation).

Environment:
  AWS_REGION        — target region, default us-west-2
  AGENT_RUNTIME_ARN — deployed Market Trends Agent runtime ARN.
                      Falls back to reading .agent_arn in the project root.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("deploy-evaluators")

REGION = os.environ.get("AWS_REGION", "us-west-2")

ROOT = Path(__file__).resolve().parent.parent  # .../evaluators
IAM_DIR = ROOT / "iam"

# Role used by AgentCore Evaluations to assume into the customer account
# and invoke the evaluator Lambdas. Name intentionally scoped to this demo.
EVAL_ROLE_NAME = "MarketTrendsEvalExecutionRole"
EVAL_ROLE_POLICY_NAME = "MarketTrendsEvalPermissions"

# Per-Lambda execution role (the role the Lambda itself assumes to run).
LAMBDA_ROLE_NAME = "MarketTrendsEvalLambdaRole"
LAMBDA_ROLE_POLICY_NAME = "MarketTrendsEvalLambdaPermissions"

ONLINE_CFG_NAME = "market_trends_online_code_eval"

# Each tuple: (folder_name, lambda_function_name, evaluator_name, level, extra_timeout)
# evaluator_name regex is [a-zA-Z][a-zA-Z0-9_]{0,47} — NO HYPHENS.
EVALUATORS: List[Tuple[str, str, str, str, int]] = [
    (
        "schema_validator",
        "market-trends-eval-schema-validator",
        "mt_schema_validator",
        "TRACE",
        30,
    ),
    (
        "stock_price_drift",
        "market-trends-eval-stock-price-drift",
        "mt_stock_price_drift",
        "TRACE",
        60,
    ),
    ("pii_regex", "market-trends-eval-pii-regex", "mt_pii_regex", "TRACE", 30),
    (
        "pii_comprehend",
        "market-trends-eval-pii-comprehend",
        "mt_pii_comprehend",
        "SESSION",
        60,
    ),
    (
        "workflow_contract_gsr",
        "market-trends-eval-workflow-contract",
        "mt_workflow_contract_gsr",
        "SESSION",
        30,
    ),
]


def _resolve_agent_arn() -> str:
    """Resolve the agent runtime ARN from env var or .agent_arn file."""
    from_env = os.environ.get("AGENT_RUNTIME_ARN", "")
    if from_env:
        return from_env
    arn_file = ROOT.parent / ".agent_arn"
    if arn_file.exists():
        arn = arn_file.read_text().strip()
        if arn:
            return arn
    raise SystemExit(
        "AGENT_RUNTIME_ARN not set and .agent_arn not found. "
        "Deploy the agent first or set: export AGENT_RUNTIME_ARN=<your-runtime-arn>"
    )


def _session() -> boto3.Session:
    return boto3.Session(region_name=REGION)


def _account_id(session: boto3.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


# --------------------------------------------------------------------------- IAM


def _ensure_role(iam, role_name: str, trust_policy: Dict[str, Any], description: str) -> str:
    try:
        resp = iam.get_role(RoleName=role_name)
        LOG.info("Role %s exists", role_name)
        # Refresh trust policy in case it drifted.
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust_policy))
        return resp["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
    LOG.info("Creating role %s", role_name)
    resp = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description=description,
    )
    # IAM consistency — give STS a moment.
    time.sleep(8)
    return resp["Role"]["Arn"]


def _ensure_inline_policy(iam, role_name: str, policy_name: str, policy: Dict[str, Any]) -> None:
    iam.put_role_policy(RoleName=role_name, PolicyName=policy_name, PolicyDocument=json.dumps(policy))


def _eval_exec_role(iam) -> str:
    trust = json.loads((IAM_DIR / "trust-policy.json").read_text())
    perms = json.loads((IAM_DIR / "permissions-policy.json").read_text())
    arn = _ensure_role(
        iam,
        EVAL_ROLE_NAME,
        trust,
        description="AgentCore Evaluations: invoke Market Trends evaluator Lambdas",
    )
    _ensure_inline_policy(iam, EVAL_ROLE_NAME, EVAL_ROLE_POLICY_NAME, perms)
    LOG.info("Eval execution role ready: %s", arn)
    return arn


def _lambda_exec_role(iam) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    perms = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "CloudWatchLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ComprehendDetectPii",
                "Effect": "Allow",
                "Action": ["comprehend:DetectPiiEntities"],
                "Resource": "*",
            },
        ],
    }
    arn = _ensure_role(
        iam,
        LAMBDA_ROLE_NAME,
        trust,
        "Execution role for Market Trends evaluator Lambdas",
    )
    _ensure_inline_policy(iam, LAMBDA_ROLE_NAME, LAMBDA_ROLE_POLICY_NAME, perms)
    LOG.info("Lambda execution role ready: %s", arn)
    return arn


# ----------------------------------------------------------------------- Lambdas


def _zip_function(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(folder / "lambda_function.py", arcname="lambda_function.py")
    return buf.getvalue()


def _deploy_lambda(lam, folder_name: str, function_name: str, timeout: int, lambda_role_arn: str) -> str:
    zip_bytes = _zip_function(ROOT / folder_name)
    waiter = lam.get_waiter("function_updated")
    try:
        lam.get_function(FunctionName=function_name)
        LOG.info("Updating Lambda %s", function_name)
        lam.update_function_code(FunctionName=function_name, ZipFile=zip_bytes, Publish=True)
        waiter.wait(FunctionName=function_name)
        lam.update_function_configuration(
            FunctionName=function_name,
            Timeout=timeout,
            MemorySize=512 if "comprehend" in folder_name else 256,
            Role=lambda_role_arn,
            Environment={"Variables": {"LOG_LEVEL": "INFO"}},
        )
        waiter.wait(FunctionName=function_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        LOG.info("Creating Lambda %s", function_name)
        lam.create_function(
            FunctionName=function_name,
            Runtime="python3.12",
            Role=lambda_role_arn,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=timeout,
            MemorySize=512 if "comprehend" in folder_name else 256,
            Publish=True,
            Environment={"Variables": {"LOG_LEVEL": "INFO"}},
            Description=f"AgentCore code-based evaluator: {folder_name}",
        )
        waiter.wait(FunctionName=function_name)
    resp = lam.get_function(FunctionName=function_name)
    return resp["Configuration"]["FunctionArn"]


def _allow_agentcore_to_invoke(lam, function_name: str, account_id: str) -> None:
    """Add a resource-based policy letting AgentCore Evaluations invoke this Lambda."""
    sid = "AllowAgentCoreEvaluationsInvoke"
    try:
        lam.add_permission(
            FunctionName=function_name,
            StatementId=sid,
            Action="lambda:InvokeFunction",
            Principal="bedrock-agentcore.amazonaws.com",
            SourceAccount=account_id,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise


# ------------------------------------------------------------------- Evaluators


def _cp_client(session: boto3.Session):
    """Return a boto3 client for the AgentCore Evaluations control plane.

    Uses the production ``bedrock-agentcore-control`` service, which supports
    CreateEvaluator and CreateOnlineEvaluationConfig. Evaluators registered
    here are visible to the production data plane (bedrock-agentcore).
    """
    return session.client("bedrock-agentcore-control", region_name=REGION)


def _find_evaluator(cp, base_name: str) -> Optional[str]:
    kwargs: Dict[str, Any] = {}
    while True:
        resp = cp.list_evaluators(**kwargs)
        for item in resp.get("evaluators", []):
            eid = item.get("evaluatorId") or ""
            if eid.startswith(f"{base_name}-"):
                return eid
        token = resp.get("nextToken")
        if not token:
            return None
        kwargs = {"nextToken": token}


def _register_evaluator(cp, name: str, level: str, lambda_arn: str, timeout: int) -> str:
    existing = _find_evaluator(cp, name)
    if existing:
        LOG.info("Evaluator %s already registered: %s", name, existing)
        return existing
    LOG.info("Registering evaluator %s (%s)", name, level)
    resp = cp.create_evaluator(
        evaluatorName=name,
        level=level,
        evaluatorConfig={
            "codeBased": {
                "lambdaConfig": {
                    "lambdaArn": lambda_arn,
                    "lambdaTimeoutInSeconds": timeout,
                }
            }
        },
    )
    return resp["evaluatorId"]


def _runtime_log_group_and_service(agent_runtime_arn: str) -> Tuple[str, str]:
    # arn/.../runtime/<agent_name>-<10char-suffix>
    agent_id = agent_runtime_arn.split("/")[-1]
    log_group = f"/aws/bedrock-agentcore/runtimes/{agent_id}-DEFAULT"
    # AgentCore Runtime emits service.name as "<agent_name>.<endpoint>" — stripping
    # the trailing random suffix from the ID.
    if len(agent_id) > 11 and agent_id[-11] == "-":
        agent_name = agent_id[:-11]
    else:
        agent_name = agent_id
    service_name = f"{agent_name}.DEFAULT"
    return log_group, service_name


def _create_online_config(cp, evaluator_ids: List[str], exec_role_arn: str, agent_runtime_arn: str) -> str:
    log_group, service_name = _runtime_log_group_and_service(agent_runtime_arn)

    # If an active config with our name prefix exists, reuse it.
    kwargs: Dict[str, Any] = {}
    while True:
        resp = cp.list_online_evaluation_configs(**kwargs)
        for cfg in resp.get("onlineEvaluationConfigs", []):
            cid = cfg.get("onlineEvaluationConfigId") or ""
            if cid.startswith(f"{ONLINE_CFG_NAME}-"):
                LOG.info("Online eval config already exists: %s", cid)
                return cid
        token = resp.get("nextToken")
        if not token:
            break
        kwargs = {"nextToken": token}

    LOG.info("Creating online eval config with %d evaluators", len(evaluator_ids))
    resp = cp.create_online_evaluation_config(
        onlineEvaluationConfigName=ONLINE_CFG_NAME,
        description="Market Trends Agent — code-based evaluators",
        rule={
            "samplingConfig": {"samplingPercentage": 100.0},
            "sessionConfig": {"sessionTimeoutMinutes": 5},
        },
        dataSourceConfig={
            "cloudWatchLogs": {
                # Runtime log group: OTEL log records from ADOT exporter
                # aws/spans: structured OTel span records with gen_ai.tool.name,
                #             session.id, and other evaluation-relevant attributes
                "logGroupNames": [log_group, "aws/spans"],
                "serviceNames": [service_name],
            }
        },
        evaluators=[{"evaluatorId": eid} for eid in evaluator_ids],
        evaluationExecutionRoleArn=exec_role_arn,
        enableOnCreate=True,
    )
    cid = resp["onlineEvaluationConfigId"]
    LOG.info("Online eval config created: %s", cid)
    return cid


# ------------------------------------------------------------------------ main


def main() -> int:
    agent_runtime_arn = _resolve_agent_arn()

    session = _session()
    account_id = _account_id(session)
    LOG.info("Account=%s Region=%s Agent=%s", account_id, REGION, agent_runtime_arn)

    iam = session.client("iam")
    lam = session.client("lambda")
    cp = _cp_client(session)

    eval_exec_role_arn = _eval_exec_role(iam)
    lambda_role_arn = _lambda_exec_role(iam)

    evaluator_ids: List[str] = []
    for folder, fn_name, ev_name, level, timeout in EVALUATORS:
        fn_arn = _deploy_lambda(lam, folder, fn_name, timeout, lambda_role_arn)
        _allow_agentcore_to_invoke(lam, fn_name, account_id)
        eid = _register_evaluator(cp, ev_name, level, fn_arn, timeout)
        evaluator_ids.append(eid)

    cfg_id = _create_online_config(cp, evaluator_ids, eval_exec_role_arn, agent_runtime_arn)

    summary = {
        "accountId": account_id,
        "region": REGION,
        "agentRuntimeArn": agent_runtime_arn,
        "evaluationExecutionRoleArn": eval_exec_role_arn,
        "lambdaExecutionRoleArn": lambda_role_arn,
        "evaluators": dict(zip([ev[2] for ev in EVALUATORS], evaluator_ids)),
        "onlineEvaluationConfigId": cfg_id,
        "onlineResultsLogGroup": f"/aws/bedrock-agentcore/evaluations/results/{cfg_id}",
    }
    out = Path(__file__).resolve().parent / ".deploy_output.json"
    out.write_text(json.dumps(summary, indent=2))
    LOG.info("Wrote deployment summary to %s", out)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
