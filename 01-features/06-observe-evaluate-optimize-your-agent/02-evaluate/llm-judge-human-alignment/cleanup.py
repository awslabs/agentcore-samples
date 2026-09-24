"""Delete the AWS resources created by this sample.

Removes the custom evaluators created by 05_run_batch_evaluation.py, the AgentCore
Runtime, its IAM role and inline policy, and the uploaded deployment package. The S3
bucket and the CloudWatch log groups are kept because other samples may share them.

Usage:
    python cleanup.py [--keep-agent] [--keep-evaluators]
"""

import argparse
import time

import boto3
from botocore.exceptions import ClientError
from common import AGENT_CONFIG_FILE, EVALUATOR_IDS_FILE, read_json


def ignore_missing(action, description: str) -> None:
    try:
        action()
        print(f"  Deleted {description}")
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("ResourceNotFoundException", "NoSuchEntity", "NoSuchKey"):
            raise
        print(f"  Already gone: {description}")


def delete_evaluators(control) -> None:
    if not EVALUATOR_IDS_FILE.exists():
        print("  No evaluator_ids.json, skipping evaluators")
        return
    for label, evaluator_id in read_json(EVALUATOR_IDS_FILE).items():
        ignore_missing(
            lambda eid=evaluator_id: control.delete_evaluator(evaluatorId=eid), f"evaluator {label} ({evaluator_id})"
        )
    EVALUATOR_IDS_FILE.unlink()


def delete_agent(config: dict) -> None:
    control = boto3.client("bedrock-agentcore-control", region_name=config["region"])
    iam = boto3.client("iam")
    s3 = boto3.client("s3", region_name=config["region"])

    ignore_missing(
        lambda: control.delete_agent_runtime(agentRuntimeId=config["agent_id"]), f"runtime {config['agent_id']}"
    )
    for _ in range(40):
        try:
            control.get_agent_runtime(agentRuntimeId=config["agent_id"])
        except ClientError:
            break
        time.sleep(5)

    ignore_missing(
        lambda: iam.delete_role_policy(RoleName=config["role_name"], PolicyName=config["policy_name"]),
        f"inline policy {config['policy_name']}",
    )
    ignore_missing(lambda: iam.delete_role(RoleName=config["role_name"]), f"role {config['role_name']}")
    ignore_missing(
        lambda: s3.delete_object(Bucket=config["s3_bucket"], Key=config["s3_key"]),
        f"s3://{config['s3_bucket']}/{config['s3_key']}",
    )
    AGENT_CONFIG_FILE.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep-agent", action="store_true", help="Keep the runtime, role, and package")
    parser.add_argument("--keep-evaluators", action="store_true", help="Keep the custom evaluators")
    args = parser.parse_args()

    if not AGENT_CONFIG_FILE.exists():
        raise SystemExit("agent_config.json not found; nothing to clean up.")
    config = read_json(AGENT_CONFIG_FILE)

    if not args.keep_evaluators:
        delete_evaluators(boto3.client("bedrock-agentcore-control", region_name=config["region"]))
    if not args.keep_agent:
        delete_agent(config)


if __name__ == "__main__":
    main()
