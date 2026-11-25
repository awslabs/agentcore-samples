"""Turn on Continuous Insights for the deployed claims agent.

Steps (each is safe to re-run):
  1. Make sure the account's Agent Insight Store is ACTIVE and ENABLED.
  2. Confirm the application name (the service.name your agent really emits).
  3. Create the evaluation execution role.
  4. Create the online evaluation config with Builtin.FailureAnalysis.

Usage:
    python scripts/setup_insights.py --name ClaimsAgent
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from botocore.exceptions import ClientError
from common import (
    client,
    evaluation_clients,
    load_state,
    poll,
    retry_call,
    save_state,
    validate_name,
)

STORE_ID = "default-insight-store"  # the service creates exactly one Store per account
# Short idle timeout so a walkthrough sees results quickly; use a value that matches
# how long your real sessions stay quiet before they are over.
SESSION_TIMEOUT_MINUTES = 2


def ensure_store_enabled(control) -> None:
    store = control.get_agent_insight_store(agentInsightStoreId=STORE_ID)
    print(f"Agent Insight Store: status={store['status']} enablement={store['enablementStatus']}")
    if store["status"] == "ACTIVE" and store["enablementStatus"] == "ENABLED":
        return
    if store["enablementStatus"] != "DISABLED" or store["status"] not in ("INACTIVE", "DISABLED"):
        sys.exit(f"Store is {store['status']}/{store['enablementStatus']}; wait for it to settle, then re-run.")
    print("Store is disabled; enabling it ...")
    control.update_agent_insight_store(
        agentInsightStoreId=STORE_ID, enablementStatus="ENABLED", clientToken=str(uuid.uuid4())
    )

    def settled() -> dict | None:
        current = control.get_agent_insight_store(agentInsightStoreId=STORE_ID)
        return current if current["status"] == "ACTIVE" else None

    poll(settled, timeout_s=150, interval_s=5, what="the Agent Insight Store to become ACTIVE")


def emitted_service_names(logs, log_group: str) -> set[str]:
    """service.name values actually present in the runtime's log group over the last hour."""
    now = int(time.time())
    query_id = logs.start_query(
        logGroupName=log_group,
        startTime=now - 3600,
        endTime=now,
        queryString="fields `resource.attributes.service.name` as svc | filter ispresent(svc) | stats count(*) by svc",
    )["queryId"]

    def finished() -> dict | None:
        result = logs.get_query_results(queryId=query_id)
        if result["status"] in ("Failed", "Cancelled", "Timeout"):
            sys.exit(f"CloudWatch Logs query {result['status'].lower()}.")
        return result if result["status"] == "Complete" else None

    result = poll(finished, timeout_s=120, interval_s=3, what="the CloudWatch Logs query")
    return {field["value"] for row in result["results"] for field in row if field["field"] == "svc"}


def evaluation_role_documents(account: str, region: str, extra_statement: dict | None = None) -> tuple[dict, dict]:
    """Trust and permissions for the evaluation execution role, following the public prerequisites page.

    extra_statement is appended to the trust policy as one more statement, for accounts that need an
    additional trusted principal or condition.
    """
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "TrustPolicyStatement",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account, "aws:ResourceAccount": account},
                    "ArnLike": {
                        "aws:SourceArn": [
                            f"arn:aws:bedrock-agentcore:{region}:{account}:evaluator/*",
                            f"arn:aws:bedrock-agentcore:{region}:{account}:online-evaluation-config/*",
                        ]
                    },
                },
            }
        ],
    }
    if extra_statement:
        trust["Statement"].append(extra_statement)
    permissions = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "CloudWatchLogRead",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups", "logs:GetQueryResults", "logs:StartQuery", "logs:StopQuery"],
                "Resource": "*",
            },
            {
                "Sid": "CloudWatchLogWrite",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/evaluations/*",
            },
            {
                "Sid": "BedrockInvoke",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    f"arn:aws:bedrock:{region}::foundation-model/*",
                    f"arn:aws:bedrock:{region}:{account}:inference-profile/*",
                ],
            },
        ],
    }
    return trust, permissions


def load_trust_statement(path: str) -> dict:
    """Read one IAM trust statement from a JSON file and check it has the required parts."""
    statement = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [
        key for key in ("Effect", "Principal", "Action") if not isinstance(statement, dict) or key not in statement
    ]
    if missing:
        sys.exit(f"{path} must hold one trust statement with Effect, Principal and Action (missing: {missing}).")
    return statement


def ensure_evaluation_role(iam, role_name: str, account: str, region: str, extra_statement: dict | None) -> str:
    trust, permissions = evaluation_role_documents(account, region, extra_statement)
    try:
        arn = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Evaluation execution role for the Continuous Insights sample",
        )["Role"]["Arn"]
        print(f"Created IAM role {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust))
        print(f"IAM role {role_name} exists; trust policy refreshed")
    iam.put_role_policy(RoleName=role_name, PolicyName="EvaluationAccess", PolicyDocument=json.dumps(permissions))
    return arn


def find_config_id(control, config_name: str) -> str | None:
    token = None
    while True:
        page = control.list_online_evaluation_configs(**({"nextToken": token} if token else {}))
        for config in page["onlineEvaluationConfigs"]:
            if config["onlineEvaluationConfigName"] == config_name:
                return config["onlineEvaluationConfigId"]
        token = page.get("nextToken")
        if not token:
            return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--extra-trust-statement",
        metavar="FILE",
        help="JSON file with one more trust statement to add to the evaluation role "
        "(only needed if role creation fails with 'cannot be assumed')",
    )
    args = parser.parse_args()
    name = validate_name(args.name)
    extra_statement = load_trust_statement(args.extra_trust_statement) if args.extra_trust_statement else None
    state = load_state(name)
    region, account = state["region"], state["account_id"]
    control, _ = evaluation_clients(region)

    ensure_store_enabled(control)

    emitted = emitted_service_names(client(region, "logs"), state["log_group"])
    if state["service_name"] not in emitted:
        sys.exit(
            f"Expected service.name {state['service_name']!r} but the log group has {sorted(emitted) or 'no spans yet'}. "
            "Invoke the agent once, wait ~2 minutes for ingestion, and re-run."
        )
    print(f"Application name confirmed: {state['service_name']}")

    role_name = f"AgentCoreEvaluationRole-{name}"  # PassRole grants are commonly scoped to this prefix
    role_arn = ensure_evaluation_role(client(region, "iam"), role_name, account, region, extra_statement)

    config_name = f"{name}_insights"
    config_id = state.get("online_config_id") or find_config_id(control, config_name)
    if config_id:
        print(f"Online evaluation config already exists: {config_id}")
    else:
        client_token = str(uuid.uuid4())  # one token for every retry, so a retried create cannot make two configs
        try:
            created = retry_call(
                lambda: control.create_online_evaluation_config(
                    clientToken=client_token,
                    onlineEvaluationConfigName=config_name,
                    description="Continuous Insights sample: Builtin.FailureAnalysis over every session",
                    # 100% sampling: grouping needs every failing session as evidence.
                    rule={
                        "samplingConfig": {"samplingPercentage": 100.0},
                        "sessionConfig": {"sessionTimeoutMinutes": SESSION_TIMEOUT_MINUTES},
                    },
                    dataSourceConfig={
                        "cloudWatchLogs": {
                            "logGroupNames": [state["log_group"]],
                            "serviceNames": [state["service_name"]],
                        }
                    },
                    # Builtin.FailureAnalysis must be the only evaluator for Continuous Insights.
                    evaluators=[{"evaluatorId": "Builtin.FailureAnalysis"}],
                    evaluationExecutionRoleArn=role_arn,
                    enableOnCreate=True,
                ),
                # A role created seconds ago may not be assumable yet (IAM is eventually consistent).
                should_retry=lambda err: "cannot be assumed" in err.response["Error"]["Message"],
                attempts=4,
                delay_s=10,
            )
        except ClientError as err:
            sys.exit(
                f"CreateOnlineEvaluationConfig failed: {err.response['Error']['Code']}: "
                f"{err.response['Error']['Message']}"
            )
        config_id = created["onlineEvaluationConfigId"]
        print(f"Created online evaluation config {config_id}")

    def active() -> dict | None:
        config = control.get_online_evaluation_config(onlineEvaluationConfigId=config_id)
        print(f"  config status={config['status']} execution={config['executionStatus']}")
        if config["status"] in ("CREATE_FAILED", "ERROR"):
            sys.exit(f"Config {config['status']}: {config.get('failureReason', 'no reason given')}")
        return config if config["status"] == "ACTIVE" else None

    poll(active, timeout_s=400, interval_s=10, what="the online evaluation config to become ACTIVE")

    save_state(
        name,
        {**state, "evaluation_role_name": role_name, "evaluation_role_arn": role_arn, "online_config_id": config_id},
    )
    print("\nContinuous Insights is on. Next: python scripts/create_dataset.py --name", name)


if __name__ == "__main__":
    main()
