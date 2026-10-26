"""
Deploy the Egress-Controlled Code Execution sample to AgentCore Runtime.

Creates (or reuses) an AgentCore Runtime from the pre-built ``supervisor`` image,
waits until it is READY, and writes ``runtime_config.json`` for ``invoke.py`` /
``cleanup.py`` to consume.

Prerequisites (this script does NOT build images — that needs Docker + buildx +
QEMU for linux/arm64; see the README "Building and pushing the images" section):
  * The three images pushed to ECR: ``supervisor``, ``broker``, ``agent``.
  * An IAM execution role AgentCore can assume (trust ``bedrock-agentcore.amazonaws.com``)
    with permissions to pull from ECR and write CloudWatch Logs.

Usage:
    python deploy.py
"""

import json
import os
import sys
import time

import boto3
from boto3.session import Session

# --- Parameters (edit for your own account) --------------------------------
ROLE_NAME = "EgressCodingExecutionRole"  # IAM execution role name...
ROLE_ARN = None  # ...or set the full ARN here to override ROLE_NAME
ECR_REPO = "egress-coding-execution"  # base ECR repo (holds supervisor/broker/agent)
IMAGE_TAG = "latest"  # image tag to use for all three images
RUNTIME_NAME = "egress_coding_execution_demo"  # AgentCore Runtime name (created or reused)

CONFIG_FILE = "runtime_config.json"

session = Session()
REGION = session.region_name or "us-east-1"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]  # never hardcode

ECR_BASE = f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPO}"
SUPERVISOR_IMAGE = f"{ECR_BASE}/supervisor:{IMAGE_TAG}"
BROKER_IMAGE = f"{ECR_BASE}/broker:{IMAGE_TAG}"
AGENT_IMAGE = f"{ECR_BASE}/agent:{IMAGE_TAG}"

control = boto3.client("bedrock-agentcore-control", region_name=REGION)


def check_images_exist():
    """Fail fast with a clear message if any of the three images is missing."""
    ecr = boto3.client("ecr", region_name=REGION)
    for component in ("supervisor", "broker", "agent"):
        repo = f"{ECR_REPO}/{component}"
        try:
            ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": IMAGE_TAG}])
        except Exception as e:  # noqa: BLE001 - surface any ECR lookup failure clearly
            print(
                f"✗ Image {repo}:{IMAGE_TAG} not found in ECR ({e}).\n"
                "  Build and push the three images first — see the README "
                '"Building and pushing the images" section.'
            )
            sys.exit(1)
    print("✓ All three images present in ECR")


def create_execution_role():
    """Create (or reuse) the AgentCore execution role, returning its ARN.

    Unlike the LLM-calling samples, this role grants no ``bedrock:InvokeModel`` —
    the supervisor only needs to pull the three images from ECR and write logs.
    """
    iam = boto3.client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:*"},
                },
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ECRPull",
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchCheckLayerAvailability",
                ],
                "Resource": "*",
            },
            {
                "Sid": "Logs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": "*",
            },
        ],
    }
    created = False
    try:
        role_arn = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Execution role for the Egress-Controlled Code Execution sample",
        )["Role"]["Arn"]
        created = True
        print(f"✓ Created IAM role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{ROLE_NAME}"
        print(f"✓ Reusing existing IAM role: {role_arn}")

    # Attach the policy first, THEN wait — CreateAgentRuntime validates the role's
    # ECR permissions, so the policy (not just the role) must have propagated.
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="ece-ecr-logs",
        PolicyDocument=json.dumps(policy),
    )
    if created:
        print("  waiting for the new role + policy to propagate ...")
        time.sleep(15)
    return role_arn


def find_runtime(name):
    """Return (agentRuntimeId, agentRuntimeArn) for an existing runtime, or None."""
    kwargs = {}
    while True:
        resp = control.list_agent_runtimes(**kwargs)
        for rt in resp.get("agentRuntimes", []):
            if rt.get("agentRuntimeName") == name:
                return rt["agentRuntimeId"], rt["agentRuntimeArn"]
        token = resp.get("nextToken")
        if not token:
            return None
        kwargs = {"nextToken": token}


def wait_until_ready(runtime_id, timeout=600, interval=15):
    """Poll GetAgentRuntime until READY (or raise on a terminal/failed state)."""
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = resp["status"]
        if status != last_status:
            print(f"  status: {status}")
            last_status = status
        if status == "READY":
            return
        if "FAILED" in status or status in ("DELETING",):
            reason = resp.get("failureReason", "(no reason provided)")
            raise RuntimeError(f"Runtime entered {status}: {reason}")
        time.sleep(interval)
    raise TimeoutError(f"Runtime not READY within {timeout}s (last: {last_status})")


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print(f"Account   : {ACCOUNT_ID}")
    print(f"Region    : {REGION}")
    print(f"Supervisor: {SUPERVISOR_IMAGE}\n")

    check_images_exist()

    # Use an explicitly provided role ARN, otherwise create/reuse ours. We record
    # whether we own the role so cleanup only deletes a role this script created.
    role_created_by_us = ROLE_ARN is None
    role_arn = ROLE_ARN or create_execution_role()

    existing = find_runtime(RUNTIME_NAME)
    if existing:
        runtime_id, runtime_arn = existing
        print(f"✓ Reusing existing runtime '{RUNTIME_NAME}': {runtime_id}")
    else:
        print(f"Creating runtime '{RUNTIME_NAME}' ...")
        created = control.create_agent_runtime(
            agentRuntimeName=RUNTIME_NAME,
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": SUPERVISOR_IMAGE}},
            roleArn=role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            description="Egress-Controlled Code Execution supervisor (sandboxed untrusted code)",
        )
        runtime_id = created["agentRuntimeId"]
        runtime_arn = created["agentRuntimeArn"]
        print(f"✓ Created runtime: {runtime_id}")

    print("\nWaiting for runtime to become READY ...")
    wait_until_ready(runtime_id)
    print("✓ Runtime is READY")

    with open(CONFIG_FILE, "w") as f:
        json.dump(
            {
                "region": REGION,
                "runtime_id": runtime_id,
                "runtime_arn": runtime_arn,
                "broker_image": BROKER_IMAGE,
                "agent_image": AGENT_IMAGE,
                "role_name": ROLE_NAME if role_created_by_us else None,
            },
            f,
            indent=2,
        )
    print(f"\n✓ Deployment complete! Wrote {CONFIG_FILE}. Test with: python invoke.py")


if __name__ == "__main__":
    main()
