"""
Deploy a Strands + Bedrock agent to AgentCore Runtime using direct code deployment.

Steps:
1. Create an IAM execution role with AgentCore permissions
2. Install arm64 dependencies with uv, zip with agent code, upload to S3
3. Create an AgentCore Runtime with codeConfiguration
4. Wait for READY, create endpoint, save config

Prerequisites:
    - uv installed (https://docs.astral.sh/uv/getting-started/installation/)
    - AWS CLI configured with credentials

Usage:
    python deploy.py
"""

import json
import os
import shutil
import subprocess
import sys
import time

import boto3
from boto3.session import Session

# ── Configuration ────────────────────────────────────────────────────────────

AGENT_NAME = f"strands_bedrock_{int(time.time()) % 100000}"
PROTOCOL = "HTTP"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "agent.py"

# Agent source files to include in the zip (alongside pre-compiled deps)
AGENT_FILES = ["agent.py"]

# ── AWS Setup ────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{AGENT_NAME}/code.zip"

print(f"Region:     {REGION}")
print(f"Account:    {ACCOUNT_ID}")
print(f"Agent:      {AGENT_NAME}")


# ── Step 1: Create IAM Execution Role ────────────────────────────────────────


def create_execution_role() -> str:
    """Create the IAM execution role with the permissions AgentCore needs.

    Uses the official 'AgentCore direct deploy execution role' policy.
    """
    iam = boto3.client("iam", region_name=REGION)
    role_name = f"agentcore-{AGENT_NAME}-role"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT_ID}},
            }
        ],
    }

    # The official AgentCore direct deploy execution role policy.
    # This is more than just bedrock:InvokeModel — the runtime also needs:
    #
    # - CloudWatch Logs: agent logging to /aws/bedrock-agentcore/runtimes/*
    # - X-Ray: distributed tracing of agent invocations
    # - CloudWatch Metrics: runtime performance metrics (namespace: bedrock-agentcore)
    # - Bedrock: model invocation (InvokeModel, InvokeModelWithResponseStream)
    #
    # Without these permissions the runtime fails to initialize, even if
    # the agent code itself is correct.
    #
    # See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html
    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": [
                    f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": ["*"],
            },
            {
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:*",
                ],
            },
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"Execution role for {AGENT_NAME}",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"\n✓ Created IAM role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
        print(f"\n✓ IAM role exists: {role_arn}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{AGENT_NAME}-policy",
        PolicyDocument=json.dumps(inline_policy),
    )

    print("  Waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn


# ── Step 2: Build arm64 deployment package and upload to S3 ──────────────────


def build_and_upload_package():
    """Build a deployment zip with pre-compiled arm64 dependencies.

    AgentCore Runtime runs on arm64 (Graviton) microVMs. When you use direct
    code deployment, the zip must include all Python dependencies pre-compiled
    for the arm64 architecture — the runtime does NOT run `pip install` from
    a requirements.txt at startup.

    Without pre-compiled deps in the zip, the runtime's HTTP server can't
    import the required packages and fails with:
        "Runtime initialization time exceeded. Please make sure that
         initialization completes in 30s."

    We use `uv pip install --python-platform aarch64-manylinux2014` to
    download the correct wheels, then zip them alongside the agent code.

    See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html
    """
    s3 = boto3.client("s3", region_name=REGION)
    pkg_dir = "deployment_package"
    zip_file = "deployment_package.zip"

    # Create S3 bucket if needed
    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=S3_BUCKET)
        else:
            s3.create_bucket(
                Bucket=S3_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
        print(f"\n✓ Created S3 bucket: {S3_BUCKET}")
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        print(f"\n✓ S3 bucket exists: {S3_BUCKET}")

    s3.put_public_access_block(
        Bucket=S3_BUCKET,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    # Clean previous build artifacts
    if os.path.isdir(pkg_dir):
        shutil.rmtree(pkg_dir)
    if os.path.exists(zip_file):
        os.remove(zip_file)

    # Download arm64-compatible wheels into a staging directory.
    # --python-platform aarch64-manylinux2014 : targets ARM64 Linux (Graviton)
    # --python-version 3.13                   : matches the PYTHON_3_13 runtime
    # --only-binary :all:                     : only download pre-built wheels (no source builds)
    # --target deployment_package             : install into a local directory (not site-packages)
    print("\n  Installing arm64 dependencies with uv...")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            "3.13",
            "--target",
            pkg_dir,
            "--only-binary",
            ":all:",
            "-r",
            "requirements.txt",
        ],
        check=True,
    )

    # Create zip: dependencies at the root (so Python can import them),
    # then agent source files also at the root.
    print("  Creating deployment zip...")
    subprocess.run(
        ["zip", "-r", f"../{zip_file}", "."],
        cwd=pkg_dir,
        check=True,
        capture_output=True,
    )
    for src_file in AGENT_FILES:
        subprocess.run(["zip", zip_file, src_file], check=True, capture_output=True)

    zip_size = os.path.getsize(zip_file) / (1024 * 1024)
    print(f"  ✓ Package: {zip_file} ({zip_size:.1f} MB)")

    # Upload to S3
    print(f"  Uploading to s3://{S3_BUCKET}/{S3_PREFIX}...")
    s3.upload_file(zip_file, S3_BUCKET, S3_PREFIX)
    print("  ✓ Uploaded")

    # Clean up local build artifacts
    shutil.rmtree(pkg_dir)
    os.remove(zip_file)


# ── Step 3: Create AgentCore Runtime ─────────────────────────────────────────


def create_runtime(role_arn: str) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print(f"\n  Creating AgentCore Runtime '{AGENT_NAME}'...")
    response = control.create_agent_runtime(
        agentRuntimeName=AGENT_NAME,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": S3_BUCKET, "prefix": S3_PREFIX}},
                "runtime": PYTHON_RUNTIME,
                "entryPoint": [ENTRY_POINT],
            }
        },
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": PROTOCOL},
        description="Strands agent with Bedrock model — tutorial example",
    )

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    print(f"  ✓ Runtime created: {runtime_id}")

    print("  Waiting for runtime to be ready...")
    while True:
        status_resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = status_resp["status"]
        print(f"    Status: {status}")
        if status == "READY":
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            print(f"  ✗ Failed: {status_resp.get('failureReason', 'Unknown')}")
            sys.exit(1)
        time.sleep(15)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


# ── Step 4: Create Endpoint ──────────────────────────────────────────────────


def create_endpoint(runtime_id: str) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print("\n  Creating endpoint 'default'...")
    response = control.create_agent_runtime_endpoint(
        agentRuntimeId=runtime_id,
        name="default",
    )
    print(f"  ✓ Endpoint created: {response['agentRuntimeEndpointArn']}")

    print("  Waiting for endpoint to be ready...")
    while True:
        eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
        for ep in eps.get("runtimeEndpoints", []):
            if ep["name"] == "default":
                status = ep["status"]
                print(f"    Status: {status}")
                if status == "READY":
                    return ep
                if status in ("CREATE_FAILED", "UPDATE_FAILED"):
                    print("  ✗ Endpoint creation failed")
                    sys.exit(1)
        time.sleep(15)


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print("=" * 60)
    print(f"Deploying {AGENT_NAME} to AgentCore Runtime")
    print("  (direct code deployment — no Docker required)")
    print("=" * 60)

    role_arn = create_execution_role()
    build_and_upload_package()
    runtime = create_runtime(role_arn)
    create_endpoint(runtime["runtime_id"])

    config = {
        "agent_name": AGENT_NAME,
        "runtime_id": runtime["runtime_id"],
        "runtime_arn": runtime["runtime_arn"],
        "region": REGION,
    }
    with open("runtime_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print("\n" + "=" * 60)
    print("✓ Deployment complete!")
    print(f"  Runtime ARN: {runtime['runtime_arn']}")
    print("  Config saved to: runtime_config.json")
    print("\n  Test with: python invoke.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
