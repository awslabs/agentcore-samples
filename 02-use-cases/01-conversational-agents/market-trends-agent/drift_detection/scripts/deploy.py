"""Deploy the drift detector onto an already-deployed Market Trends Agent.

Creates, idempotently:
  1. a DynamoDB table for per-evaluator detector state
  2. an IAM role for the detector Lambda
  3. the detector Lambda, packaged from drift_detection/detector/
  4. an EventBridge schedule that invokes it
  5. one CloudWatch alarm per evaluator on the DriftDetected metric
  6. a CloudWatch dashboard

Prerequisites: the agent is deployed, evaluators/scripts/deploy.py has run, and at
least one session has been evaluated so the results log group exists.

Environment:
  AWS_REGION              target region, default us-east-1
  ONLINE_EVAL_CONFIG_ID   online evaluation config to read scores from. Falls
                          back to evaluators/scripts/.deploy_output.json.
  DRIFT_WARMUP            samples before a drift claim is allowed
  DRIFT_CONSECUTIVE       consecutive raw alarms that confirm drift
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("deploy-drift-detector")

REGION = os.environ.get("AWS_REGION", "us-east-1")

ROOT = Path(__file__).resolve().parent.parent  # drift_detection/
AGENT_ROOT = ROOT.parent  # market-trends-agent/

TABLE_NAME = "market-trends-drift-detector-state"
ROLE_NAME = "MarketTrendsDriftDetectorRole"
ROLE_POLICY_NAME = "MarketTrendsDriftDetectorPermissions"
FUNCTION_NAME = "market-trends-drift-detector"
SCHEDULE_NAME = "market-trends-drift-detector-schedule"
DASHBOARD_NAME = "MarketTrendsDriftDetection"
ALARM_PREFIX = "MarketTrends-Drift"

DRIFT_NAMESPACE = os.environ.get("DRIFT_NAMESPACE", "MarketTrends/DriftDetection")
SCHEDULE_MINUTES = int(os.environ.get("DRIFT_SCHEDULE_MINUTES", "5"))

_EVAL_DEPLOY_OUTPUT = AGENT_ROOT / "evaluators" / "scripts" / ".deploy_output.json"


def _eval_deploy_output() -> Dict[str, Any]:
    if _EVAL_DEPLOY_OUTPUT.exists():
        return json.loads(_EVAL_DEPLOY_OUTPUT.read_text())
    return {}


def resolve_online_config_id() -> str:
    from_env = os.environ.get("ONLINE_EVAL_CONFIG_ID", "")
    if from_env:
        return from_env
    cid = _eval_deploy_output().get("onlineEvaluationConfigId", "")
    if cid:
        return cid
    raise SystemExit(
        "ONLINE_EVAL_CONFIG_ID not set and evaluators/scripts/.deploy_output.json "
        "not found. Run evaluators/scripts/deploy.py first."
    )


def resolve_service_name() -> str:
    """The service.name dimension AgentCore Observability publishes for this agent.

    Must match exactly what evaluators/scripts/deploy.py computed when it created
    the online evaluation config, or the dashboard's raw-scores widget queries the
    wrong service.name and silently renders empty. Derived the same way that
    script derives it (agentRuntimeArn stripped of its runtime suffix), read back
    from its .deploy_output.json rather than recomputed by guesswork here.
    """
    from_env = os.environ.get("SERVICE_NAME", "")
    if from_env:
        return from_env

    arn = _eval_deploy_output().get("agentRuntimeArn", "")
    if arn:
        agent_id = arn.split("/")[-1]
        agent_name = agent_id[:-11] if len(agent_id) > 11 and agent_id[-11] == "-" else agent_id
        return f"{agent_name}.DEFAULT"

    LOG.warning(
        "Could not derive SERVICE_NAME from evaluators/scripts/.deploy_output.json; "
        "falling back to a guessed default. The dashboard's raw evaluator score "
        "widget will be empty if this does not match the agent's real service.name. "
        "Set SERVICE_NAME explicitly or run evaluators/scripts/deploy.py first."
    )
    return "markettrends_market_trends_agent.DEFAULT"


SERVICE_NAME = resolve_service_name()


# ------------------------------------------------------------------ DynamoDB


def ensure_table(ddb) -> None:
    try:
        ddb.describe_table(TableName=TABLE_NAME)
        LOG.info("State table %s exists", TABLE_NAME)
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    LOG.info("Creating state table %s", TABLE_NAME)
    ddb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "evaluator", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "evaluator", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.get_waiter("table_exists").wait(TableName=TABLE_NAME)
    LOG.info("State table ready")


# ----------------------------------------------------------------------- IAM


def ensure_role(iam, account_id: str, online_cfg_id: str) -> str:
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
                "Sid": "CloudWatchLogsForItself",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{REGION}:{account_id}:log-group:/aws/lambda/{FUNCTION_NAME}*",
            },
            {
                "Sid": "ReadEvaluationResults",
                "Effect": "Allow",
                "Action": ["logs:FilterLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"],
                "Resource": f"arn:aws:logs:{REGION}:{account_id}:log-group:/aws/bedrock-agentcore/evaluations/results/{online_cfg_id}:*",
            },
            {
                "Sid": "DetectorState",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Scan",
                ],
                "Resource": f"arn:aws:dynamodb:{REGION}:{account_id}:table/{TABLE_NAME}",
            },
            {
                # PutMetricData cannot be scoped to a namespace by resource, only
                # by a condition key, so this is the tightest form available.
                "Sid": "PublishDriftMetrics",
                "Effect": "Allow",
                "Action": ["cloudwatch:PutMetricData"],
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": DRIFT_NAMESPACE}},
            },
        ],
    }

    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        LOG.info("Role %s exists", ROLE_NAME)
        iam.update_assume_role_policy(RoleName=ROLE_NAME, PolicyDocument=json.dumps(trust))
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        LOG.info("Creating role %s", ROLE_NAME)
        arn = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Market Trends Agent quality drift detector",
        )["Role"]["Arn"]
        # IAM propagation. Lambda rejects a role it cannot yet resolve, and the
        # failure surfaces as an unrelated-looking KMS grant error.
        LOG.info("Waiting for IAM propagation")
        time.sleep(12)

    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY_NAME, PolicyDocument=json.dumps(perms))
    return arn


# -------------------------------------------------------------------- Lambda


def build_package() -> bytes:
    """Zip drift_detection/detector/ as a Lambda deployment package.

    Only stdlib and boto3 are used, so there is nothing to vendor.
    """
    buf = io.BytesIO()
    detector_dir = ROOT / "detector"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(detector_dir.glob("*.py")):
            zf.write(path, arcname=f"drift_detection/detector/{path.name}")
        zf.writestr("drift_detection/__init__.py", "")
    return buf.getvalue()


def ensure_function(lam, role_arn: str, online_cfg_id: str) -> str:
    env = {
        "Variables": {
            "STATE_TABLE": TABLE_NAME,
            "ONLINE_EVAL_CONFIG_ID": online_cfg_id,
            "SERVICE_NAME": SERVICE_NAME,
            "DRIFT_NAMESPACE": DRIFT_NAMESPACE,
            "DRIFT_WARMUP": os.environ.get("DRIFT_WARMUP", "100"),
            "DRIFT_CONSECUTIVE": os.environ.get("DRIFT_CONSECUTIVE", "5"),
            "DRIFT_LOOKBACK_SECONDS": os.environ.get("DRIFT_LOOKBACK_SECONDS", str(6 * 3600)),
            "LOG_LEVEL": "INFO",
        }
    }
    pkg = build_package()
    waiter = lam.get_waiter("function_updated")

    try:
        lam.get_function(FunctionName=FUNCTION_NAME)
        LOG.info("Updating Lambda %s", FUNCTION_NAME)
        lam.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=pkg, Publish=True)
        waiter.wait(FunctionName=FUNCTION_NAME)
        lam.update_function_configuration(
            FunctionName=FUNCTION_NAME,
            Role=role_arn,
            Handler="drift_detection.detector.handler.lambda_handler",
            Timeout=120,
            MemorySize=256,
            Environment=env,
        )
        waiter.wait(FunctionName=FUNCTION_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        LOG.info("Creating Lambda %s", FUNCTION_NAME)
        lam.create_function(
            FunctionName=FUNCTION_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="drift_detection.detector.handler.lambda_handler",
            Code={"ZipFile": pkg},
            Timeout=120,
            MemorySize=256,
            Publish=True,
            Environment=env,
            Description="Quality drift detection over Market Trends Agent evaluator scores",
        )
        waiter.wait(FunctionName=FUNCTION_NAME)

    return lam.get_function(FunctionName=FUNCTION_NAME)["Configuration"]["FunctionArn"]


# ---------------------------------------------------------------- Scheduling


def ensure_schedule(events, lam, function_arn: str, account_id: str) -> None:
    rule_arn = events.put_rule(
        Name=SCHEDULE_NAME,
        ScheduleExpression=f"rate({SCHEDULE_MINUTES} minutes)",
        State="ENABLED",
        Description="Invoke the Market Trends drift detector",
    )["RuleArn"]

    try:
        lam.add_permission(
            FunctionName=FUNCTION_NAME,
            StatementId="AllowEventBridgeInvoke",
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=rule_arn,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise

    events.put_targets(Rule=SCHEDULE_NAME, Targets=[{"Id": "detector", "Arn": function_arn}])
    LOG.info("Schedule %s every %d minutes", SCHEDULE_NAME, SCHEDULE_MINUTES)


# --------------------------------------------------------- Alarms, dashboard


def ensure_alarms(cw, evaluators: List[Any]) -> List[str]:
    """One alarm per evaluator, so the alarm names which stream drifted.

    A single aggregate alarm would tell an operator that something degraded
    without saying what, which is the least useful moment to start guessing.
    """
    names = []
    for ev in evaluators:
        alarm_name = f"{ALARM_PREFIX}-{ev.evaluator}"
        cw.put_metric_alarm(
            AlarmName=alarm_name,
            AlarmDescription=(
                f"Quality drift on {ev.evaluator} ({ev.shape} score stream, {ev.method} detector). {ev.note}"
            ),
            Namespace=DRIFT_NAMESPACE,
            MetricName="DriftDetected",
            Dimensions=[
                {"Name": "Evaluator", "Value": ev.evaluator},
                {"Name": "ServiceName", "Value": SERVICE_NAME},
            ],
            Statistic="Maximum",
            Period=300,
            EvaluationPeriods=1,
            Threshold=1.0,
            ComparisonOperator="GreaterThanOrEqualToThreshold",
            # The detector has already applied its own persistence rule before
            # emitting a 1, so the alarm does not need to re-confirm. Missing data
            # is not breaching: a gap means no evaluations arrived, not that
            # quality is fine.
            TreatMissingData="missing",
        )
        names.append(alarm_name)
    LOG.info("Configured %d drift alarms", len(names))
    return names


def ensure_dashboard(cw, evaluators: List[Any]) -> None:
    def series(metric: str):
        return [[DRIFT_NAMESPACE, metric, "Evaluator", ev.evaluator, "ServiceName", SERVICE_NAME] for ev in evaluators]

    widgets = [
        {
            "type": "text",
            "x": 0,
            "y": 0,
            "width": 24,
            "height": 3,
            "properties": {
                "markdown": (
                    "# Market Trends Agent quality drift\n"
                    "**DriftDetected** is 1 while a stream is confirmed drifted and latched. "
                    "**DriftPressure** is how close each stream is to its limit, where 1.0 is at "
                    "the limit. **WarmingUp** at 1 means that detector has not seen enough "
                    "samples to make any claim yet, so a quiet detector is not yet evidence of "
                    "health. **BaselineMean** is each detector's own reference, learned from "
                    "this agent's history rather than a fixed threshold."
                )
            },
        },
        {
            "type": "metric",
            "x": 0,
            "y": 3,
            "width": 12,
            "height": 7,
            "properties": {
                "title": "Drift detected (latched)",
                "metrics": series("DriftDetected"),
                "view": "timeSeries",
                "stat": "Maximum",
                "period": 300,
                "region": REGION,
                "yAxis": {"left": {"min": 0, "max": 1.2}},
            },
        },
        {
            "type": "metric",
            "x": 12,
            "y": 3,
            "width": 12,
            "height": 7,
            "properties": {
                "title": "Pressure toward the limit (1.0 = at limit)",
                "metrics": series("DriftPressure"),
                "view": "timeSeries",
                "stat": "Maximum",
                "period": 300,
                "region": REGION,
                "annotations": {"horizontal": [{"label": "limit", "value": 1.0}]},
            },
        },
        {
            "type": "metric",
            "x": 0,
            "y": 10,
            "width": 12,
            "height": 7,
            "properties": {
                "title": "Baseline mean per evaluator",
                "metrics": series("BaselineMean"),
                "view": "timeSeries",
                "stat": "Average",
                "period": 300,
                "region": REGION,
            },
        },
        {
            "type": "metric",
            "x": 12,
            "y": 10,
            "width": 12,
            "height": 7,
            "properties": {
                "title": "Samples seen (warm-up progress)",
                "metrics": series("SamplesSeen"),
                "view": "timeSeries",
                "stat": "Maximum",
                "period": 300,
                "region": REGION,
            },
        },
        {
            "type": "metric",
            "x": 0,
            "y": 17,
            "width": 24,
            "height": 7,
            "properties": {
                "title": "Raw evaluator scores from AgentCore Evaluations",
                "metrics": [
                    [
                        "Bedrock-AgentCore/Evaluations",
                        ev.evaluator,
                        "service.name",
                        SERVICE_NAME,
                        "onlineEvaluationConfigId",
                        resolve_online_config_id(),
                    ]
                    for ev in evaluators
                ],
                "view": "timeSeries",
                "stat": "Average",
                "period": 300,
                "region": REGION,
                "yAxis": {"left": {"min": 0, "max": 1.05}},
            },
        },
    ]

    cw.put_dashboard(DashboardName=DASHBOARD_NAME, DashboardBody=json.dumps({"widgets": widgets}))
    LOG.info("Dashboard %s updated", DASHBOARD_NAME)


# ------------------------------------------------------------------------ main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-invoke", action="store_true", help="do not run the detector once after deploying")
    args = parser.parse_args()

    sys.path.insert(0, str(AGENT_ROOT))
    from drift_detection.detector import config as cfg

    online_cfg_id = resolve_online_config_id()
    session = boto3.Session(region_name=REGION)
    account_id = session.client("sts").get_caller_identity()["Account"]
    LOG.info("Account=%s Region=%s OnlineEvalConfig=%s", account_id, REGION, online_cfg_id)

    ddb = session.client("dynamodb")
    iam = session.client("iam")
    lam = session.client("lambda")
    events = session.client("events")
    cw = session.client("cloudwatch")

    ensure_table(ddb)
    role_arn = ensure_role(iam, account_id, online_cfg_id)
    function_arn = ensure_function(lam, role_arn, online_cfg_id)
    ensure_schedule(events, lam, function_arn, account_id)
    alarms = ensure_alarms(cw, cfg.ALL_EVALUATORS)
    ensure_dashboard(cw, cfg.ALL_EVALUATORS)

    if not args.skip_invoke:
        LOG.info("Invoking the detector once")
        resp = lam.invoke(FunctionName=FUNCTION_NAME, Payload=b"{}")
        payload = json.loads(resp["Payload"].read() or b"{}")
        LOG.info("First run: %s", json.dumps(payload)[:600])

    summary = {
        "region": REGION,
        "stateTable": TABLE_NAME,
        "roleArn": role_arn,
        "functionArn": function_arn,
        "scheduleRule": SCHEDULE_NAME,
        "scheduleMinutes": SCHEDULE_MINUTES,
        "onlineEvaluationConfigId": online_cfg_id,
        "driftNamespace": DRIFT_NAMESPACE,
        "alarms": alarms,
        "dashboard": DASHBOARD_NAME,
        "dashboardUrl": (
            f"https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#dashboards:name={DASHBOARD_NAME}"
        ),
        "warmup": os.environ.get("DRIFT_WARMUP", "100"),
        "consecutive": os.environ.get("DRIFT_CONSECUTIVE", "5"),
    }
    out = ROOT / "scripts" / ".deploy_output.json"
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
