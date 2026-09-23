"""
Deploy the Travel Agent with OpenLIT observability to AgentCore Runtime.

Prerequisites:
    - OpenLIT must be running and reachable from the internet or a VPC that
      AgentCore Runtime can access. See utils/travel_agent.py for deployment options.

Usage:
    cp .env.example .env  # fill in your OpenLIT OTLP endpoint
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

AGENT_NAME = f"openlit_obs_{int(time.time()) % 100000}"
PROTOCOL = "HTTP"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "travel_agent.py"
AGENT_FILES = ["utils/travel_agent.py"]

PLATFORM_ENV_VARS = {
    "OTEL_EXPORTER_OTLP_ENDPOINT": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
    "OTEL_SERVICE_NAME": os.getenv("OTEL_SERVICE_NAME", "agentcore-travel-agent"),
    "DISABLE_ADOT_OBSERVABILITY": "true",
    "BEDROCK_MODEL_ID": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
}

session = Session()
REGION = session.region_name
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{AGENT_NAME}/code.zip"

print(f"Region:  {REGION}\nAccount: {ACCOUNT_ID}\nAgent:   {AGENT_NAME}")


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
    time.sleep(10)
    return role_arn


def build_and_upload_package():
    s3 = boto3.client("s3", region_name=REGION)
    pkg_dir, zip_file = "deployment_package", "deployment_package.zip"
    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=S3_BUCKET)
        else:
            s3.create_bucket(
                Bucket=S3_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        pass
    if os.path.isdir(pkg_dir):
        shutil.rmtree(pkg_dir)
    if os.path.exists(zip_file):
        os.remove(zip_file)
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
    subprocess.run(
        ["zip", "-r", f"../{zip_file}", "."],
        cwd=pkg_dir,
        check=True,
        capture_output=True,
    )
    for src_file in AGENT_FILES:
        subprocess.run(["zip", zip_file, "-j", src_file], check=True, capture_output=True)
    s3.upload_file(zip_file, S3_BUCKET, S3_PREFIX)
    shutil.rmtree(pkg_dir)
    os.remove(zip_file)
    print(f"  Package uploaded to s3://{S3_BUCKET}/{S3_PREFIX}")


def create_runtime(role_arn: str) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
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
        description="Travel agent with OpenLIT observability",
    )
    runtime_id, runtime_arn = response["agentRuntimeId"], response["agentRuntimeArn"]
    print(f"  Runtime created: {runtime_id}")
    while True:
        status_resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = status_resp["status"]
        print(f"    Status: {status}")
        if status == "READY":
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            sys.exit(1)
        time.sleep(15)
    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


def create_endpoint(runtime_id: str):
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    control.create_agent_runtime_endpoint(agentRuntimeId=runtime_id, name="default")
    while True:
        for ep in control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id).get("runtimeEndpoints", []):
            if ep["name"] == "default":
                print(f"    Status: {ep['status']}")
                if ep["status"] == "READY":
                    return ep
                if ep["status"] in ("CREATE_FAILED", "UPDATE_FAILED"):
                    sys.exit(1)
        time.sleep(15)


def main():
    if not PLATFORM_ENV_VARS.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        print("ERROR: OTEL_EXPORTER_OTLP_ENDPOINT not set. Set your OpenLIT OTLP endpoint.")
        sys.exit(1)
    print("=" * 60)
    print("Deploying Travel Agent with OpenLIT Observability")
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
    print(f"\nDeployment complete! Runtime ARN: {runtime['runtime_arn']}")
    print("Next: python invoke.py  |  Open OpenLIT → Requests")


if __name__ == "__main__":
    main()
