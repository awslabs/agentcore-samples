"""
Deploy the Travel Agent with Arize observability to AgentCore Runtime.

Prerequisites:
    - uv installed
    - AWS credentials configured
    - .env file with ARIZE_API_KEY and ARIZE_SPACE_ID set

Usage:
    cp .env.example .env  # fill in your Arize credentials
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
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────

AGENT_NAME = f"arize_obs_{int(time.time()) % 100000}"
PROTOCOL = "HTTP"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "travel_agent.py"
AGENT_FILES = ["utils/travel_agent.py"]

# Platform-specific env vars read from .env
PLATFORM_ENV_VARS = {
    "ARIZE_API_KEY": os.getenv("ARIZE_API_KEY", ""),
    "ARIZE_SPACE_ID": os.getenv("ARIZE_SPACE_ID", ""),
    "ARIZE_ENDPOINT": os.getenv("ARIZE_ENDPOINT", "https://otlp.arize.com:443"),
    "ARIZE_PROJECT_NAME": os.getenv("ARIZE_PROJECT_NAME", "agentcore-travel-agent"),
    "DISABLE_ADOT_OBSERVABILITY": "true",
    "BEDROCK_MODEL_ID": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
}

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{AGENT_NAME}/code.zip"

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")
print(f"Agent:   {AGENT_NAME}")


def create_execution_role() -> str:
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
        print(f"\nCreated IAM role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
        print(f"\nIAM role exists: {role_arn}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{AGENT_NAME}-policy",
        PolicyDocument=json.dumps(inline_policy),
    )
    print("  Waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn


def build_and_upload_package():
    s3 = boto3.client("s3", region_name=REGION)
    pkg_dir = "deployment_package"
    zip_file = "deployment_package.zip"

    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=S3_BUCKET)
        else:
            s3.create_bucket(
                Bucket=S3_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
        print(f"\nCreated S3 bucket: {S3_BUCKET}")
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        print(f"\nS3 bucket exists: {S3_BUCKET}")

    if os.path.isdir(pkg_dir):
        shutil.rmtree(pkg_dir)
    if os.path.exists(zip_file):
        os.remove(zip_file)

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

    print("  Creating deployment zip...")
    subprocess.run(
        ["zip", "-r", f"../{zip_file}", "."],
        cwd=pkg_dir,
        check=True,
        capture_output=True,
    )
    for src_file in AGENT_FILES:
        subprocess.run(["zip", zip_file, "-j", src_file], check=True, capture_output=True)

    zip_size = os.path.getsize(zip_file) / (1024 * 1024)
    print(f"  Package: {zip_file} ({zip_size:.1f} MB)")

    print(f"  Uploading to s3://{S3_BUCKET}/{S3_PREFIX}...")
    s3.upload_file(zip_file, S3_BUCKET, S3_PREFIX)
    shutil.rmtree(pkg_dir)
    os.remove(zip_file)


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
        environmentVariables=PLATFORM_ENV_VARS,
        description="Travel agent with Arize observability",
    )

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    print(f"  Runtime created: {runtime_id}")

    print("  Waiting for runtime to be ready...")
    while True:
        status_resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = status_resp["status"]
        print(f"    Status: {status}")
        if status == "READY":
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            print(f"  Failed: {status_resp.get('failureReason', 'Unknown')}")
            sys.exit(1)
        time.sleep(15)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


def create_endpoint(runtime_id: str) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print("\n  Creating endpoint 'default'...")
    control.create_agent_runtime_endpoint(agentRuntimeId=runtime_id, name="default")

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
                    print("  Endpoint creation failed")
                    sys.exit(1)
        time.sleep(15)


def main():
    if not PLATFORM_ENV_VARS.get("ARIZE_API_KEY"):
        print("ERROR: ARIZE_API_KEY not set. Copy .env.example → .env and fill in your credentials.")
        sys.exit(1)

    print("=" * 60)
    print("Deploying Travel Agent with Arize Observability")
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
        "role_name": f"agentcore-{AGENT_NAME}-role",
        "s3_bucket": S3_BUCKET,
        "s3_prefix": S3_PREFIX,
    }
    with open("runtime_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print("\n" + "=" * 60)
    print("Deployment complete!")
    print(f"  Runtime ARN: {runtime['runtime_arn']}")
    print("  Config:      runtime_config.json")
    print("\n  Next steps:")
    print("    python invoke.py")
    print("    Open Arize dashboard → your project → Traces")
    print("=" * 60)


if __name__ == "__main__":
    main()
