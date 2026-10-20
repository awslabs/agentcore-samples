"""Deploy the TypeScript HR Assistant to Bedrock AgentCore Runtime.

Builds the Node.js Docker image, pushes it to ECR, creates an AgentCore Runtime
with the container artifact, and polls until READY. Saves connection details to
agent_config.json in this directory for use by evaluate.py.

Framework:       LangGraph TypeScript + @arizeai/openinference-instrumentation-langchain
Scope name:      @arizeai/openinference-instrumentation-langchain

Usage:
    python deploy.py [--region REGION]

Output:
    agent_config.json  — agent_id, agent_arn, cw_log_group, region, ecr_repo, role_arn

Deployment steps:
  1. Create IAM execution role for the runtime
  2. Create ECR repository (if not exists)
  3. Authenticate Docker to ECR, build the image for linux/arm64 and push it
  4. Create AgentCore Runtime via create_agent_runtime (containerConfiguration)
  5. Poll until READY
  6. Update runtime to inject OTEL_LOG_GROUP_NAME, OTEL_SERVICE_NAME and AGENT_RUNTIME_ARN
     (enables CW span export with the resource attributes evaluations use), poll until READY
  7. Write agent_config.json
"""

import argparse
import base64
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import boto3
from boto3.session import Session

_SCRIPT_DIR = Path(__file__).parent
_CONFIG_FILE = _SCRIPT_DIR / "agent_config.json"
_AGENT_DIR = _SCRIPT_DIR / "hr-assistant"

MODEL_ID = "us.amazon.nova-lite-v1:0"


def _trust_policy(account_id: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                    "Condition": {
                        "StringEquals": {"aws:SourceAccount": account_id},
                        "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:*:{account_id}:runtime/*"},
                    },
                }
            ],
        }
    )


_EXECUTION_POLICY = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                    "cloudwatch:PutMetricData",
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                ],
                "Resource": "*",
            }
        ],
    }
)


def create_execution_role(iam, role_name: str, account_id: str) -> str:
    """Create (or reuse) the runtime execution role and attach its inline policy."""
    print(f"\n[1/6] Creating IAM role '{role_name}' ...")
    try:
        role_arn = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=_trust_policy(account_id))["Role"][
            "Arn"
        ]
        print(f"  Created: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"  Already exists: {role_arn}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{role_name}_policy",
        PolicyDocument=_EXECUTION_POLICY,
    )
    print("  Policy attached. Waiting 10s for IAM propagation ...")
    time.sleep(10)
    return role_arn


def build_and_push_image(ecr, repo: str, image_uri: str) -> None:
    """Create the ECR repository, log Docker in, and build/push the linux/arm64 image."""
    print(f"\n[2/6] Creating ECR repository '{repo}' ...")
    try:
        ecr.create_repository(repositoryName=repo)
        print(f"  Created: {repo}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        print(f"  Already exists: {repo}")

    print("\n[3/6] Building Docker image for linux/arm64 ...")
    token = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(token["authorizationToken"]).decode().split(":", 1)
    login = subprocess.run(
        ["docker", "login", "--username", user, "--password-stdin", token["proxyEndpoint"]],
        input=password.encode(),
        capture_output=True,
        check=False,
    )
    if login.returncode != 0:
        print("  ERROR: Docker ECR login failed:", login.stderr.decode())
        sys.exit(1)
    print("  ECR login successful.")

    build = subprocess.run(
        ["docker", "buildx", "build", "--platform", "linux/arm64", "--tag", image_uri, "--push", str(_AGENT_DIR)],
        check=False,
    )
    if build.returncode != 0:
        print("  ERROR: Docker build/push failed.")
        sys.exit(1)
    print(f"  Image pushed: {image_uri}")


def wait_ready(ctrl, agent_id: str, label: str, timeout: int = 600) -> None:
    """Poll the runtime until it is READY."""
    print(f"\n[{label}] Waiting for READY ...")
    for elapsed in range(0, timeout, 15):
        status = ctrl.get_agent_runtime(agentRuntimeId=agent_id).get("status", "UNKNOWN")
        print(f"  [{elapsed:>3}s] {status}")
        if status in ("READY", "ACTIVE"):
            return
        if "FAILED" in status:
            raise RuntimeError(f"Deploy failed with status: {status}")
        time.sleep(15)
    raise TimeoutError(f"Agent did not reach READY in {timeout}s")


def create_runtime(ctrl, agent_name: str, image_uri: str, role_arn: str, region: str) -> tuple:
    """Create the container runtime, then point its span exporter at the runtime log group."""
    print(f"\n[4/6] Creating AgentCore Runtime '{agent_name}' ...")
    artifact = {"containerConfiguration": {"containerUri": image_uri}}
    env = {"AWS_REGION": region, "BEDROCK_MODEL_ID": MODEL_ID, "PORT": "8080"}
    created = ctrl.create_agent_runtime(
        agentRuntimeName=agent_name,
        agentRuntimeArtifact=artifact,
        networkConfiguration={"networkMode": "PUBLIC"},
        roleArn=role_arn,
        environmentVariables=env,
    )
    agent_id = created["agentRuntimeId"]
    print(f"  Runtime ID: {agent_id}")

    cw_log_group = f"/aws/bedrock-agentcore/runtimes/{agent_id}-DEFAULT"

    wait_ready(ctrl, agent_id, "5/6")

    # Custom containers get no ADOT sidecar, so the agent exports spans itself.
    # Tell it which log group to write to and which service name / runtime ARN to
    # stamp on each span (batch and online evaluation discover sessions by service
    # name). These values are only known after the runtime is created.
    print("\n[6/6] Updating runtime with span-export settings ...")
    env["OTEL_LOG_GROUP_NAME"] = cw_log_group
    env["OTEL_SERVICE_NAME"] = f"{agent_name}.DEFAULT"
    env["AGENT_RUNTIME_ARN"] = created["agentRuntimeArn"]
    ctrl.update_agent_runtime(
        agentRuntimeId=agent_id,
        agentRuntimeArtifact=artifact,
        networkConfiguration={"networkMode": "PUBLIC"},
        roleArn=role_arn,
        environmentVariables=env,
    )
    for key in ("OTEL_LOG_GROUP_NAME", "OTEL_SERVICE_NAME", "AGENT_RUNTIME_ARN"):
        print(f"  {key} = {env[key]}")
    wait_ready(ctrl, agent_id, "6/6")

    return agent_id, cw_log_group


def main() -> None:
    """Deploy the agent and write agent_config.json."""
    parser = argparse.ArgumentParser(description="Deploy the TypeScript HR Assistant to AgentCore Runtime")
    parser.add_argument("--region", default=None, help="AWS region")
    args = parser.parse_args()

    region = args.region or Session().region_name or "us-east-1"
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    iam = boto3.client("iam", region_name=region)
    ecr = boto3.client("ecr", region_name=region)
    ctrl = boto3.client("bedrock-agentcore-control", region_name=region)

    agent_name = f"hr_ts_{uuid.uuid4().hex[:8]}"
    image_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{agent_name}:latest"
    print(f"Region     : {region}")
    print(f"Agent name : {agent_name}")
    print(f"ECR image  : {image_uri}")

    role_arn = create_execution_role(iam, f"{agent_name}_role", account_id)
    build_and_push_image(ecr, agent_name, image_uri)

    agent_id, cw_log_group = create_runtime(ctrl, agent_name, image_uri, role_arn, region)
    otel_service_name = f"{agent_name}.DEFAULT"

    agent_arn = ctrl.get_agent_runtime(agentRuntimeId=agent_id)["agentRuntimeArn"]
    config = {
        "agent_id": agent_id,
        "agent_arn": agent_arn,
        "cw_log_group": cw_log_group,
        "otel_service_name": otel_service_name,
        "region": region,
        "role_arn": role_arn,
        "ecr_repo": agent_name,
        "ecr_uri": image_uri,
        "framework": "langgraph-typescript",
        "instrumentation_scope": "@arizeai/openinference-instrumentation-langchain",
    }
    _CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")

    print("\nDeploy complete.")
    print(f"  AGENT_ID       : {agent_id}")
    print(f"  AGENT_ARN      : {agent_arn}")
    print(f"  CW_LOG_GROUP   : {cw_log_group}")
    print(f"  OTel service   : {otel_service_name}")
    print(f"  Config saved   : {_CONFIG_FILE}")
    print("\nNext step:  python evaluate.py")


if __name__ == "__main__":
    main()
