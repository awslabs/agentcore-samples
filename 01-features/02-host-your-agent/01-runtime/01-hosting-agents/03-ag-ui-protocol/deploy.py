"""
Deploy an AG-UI agent to AgentCore Runtime.

Uses direct code deployment (zip to S3) — no Docker required.
Key difference: serverProtocol is set to 'AGUI'.

Usage:
    python deploy.py
"""

import json
import os
import sys
import time

import boto3
from boto3.session import Session

AGENT_NAME = "agui_doc_agent"
PROTOCOL = "AGUI"
PYTHON_RUNTIME = "PYTHON_3_12"
ENTRY_POINT = "agent.py"
CODE_FILES = ["agent.py", "requirements.txt"]

session = Session()
REGION = session.region_name
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{AGENT_NAME}/code.zip"


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
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "arn:aws:logs:*:*:*",
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
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{AGENT_NAME}-policy",
        PolicyDocument=json.dumps(inline_policy),
    )
    print(f"✓ IAM role: {role_arn}")
    time.sleep(10)
    return role_arn


def zip_and_upload_code():
    import shutil
    import subprocess

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
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        pass

    if os.path.isdir(pkg_dir):
        shutil.rmtree(pkg_dir)
    if os.path.exists(zip_file):
        os.remove(zip_file)

    python_version = PYTHON_RUNTIME.replace("PYTHON_", "").replace("_", ".").lower()
    print("  Installing arm64 dependencies with uv...")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            python_version,
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
    for f in CODE_FILES:
        if f.endswith(".py"):
            subprocess.run(["zip", zip_file, f], check=True, capture_output=True)

    zip_size = os.path.getsize(zip_file) / (1024 * 1024)
    print(f"  Package: {zip_file} ({zip_size:.1f} MB)")

    s3.upload_file(zip_file, S3_BUCKET, S3_PREFIX)
    print(f"\u2713 Code uploaded to s3://{S3_BUCKET}/{S3_PREFIX}")

    shutil.rmtree(pkg_dir)
    os.remove(zip_file)


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
        description="AG-UI document co-authoring agent",
    )

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]

    while True:
        status_resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = status_resp["status"]
        print(f"  Runtime status: {status}")
        if status == "READY":
            break
        if "FAILED" in status:
            sys.exit(1)
        time.sleep(15)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


def create_endpoint(runtime_id: str):
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    control.create_agent_runtime_endpoint(agentRuntimeId=runtime_id, name="default")

    while True:
        eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
        if eps.get("runtimeEndpoints"):
            status = eps["runtimeEndpoints"][0]["status"]
            print(f"  Endpoint status: {status}")
            if status == "READY":
                return
            if "FAILED" in status:
                sys.exit(1)
        time.sleep(15)


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"Deploying {AGENT_NAME} (direct code deployment — no Docker required)\n")
    role_arn = create_execution_role()
    zip_and_upload_code()
    runtime = create_runtime(role_arn)
    create_endpoint(runtime["runtime_id"])

    with open("runtime_config.json", "w") as f:
        json.dump(
            {
                "agent_name": AGENT_NAME,
                "runtime_id": runtime["runtime_id"],
                "runtime_arn": runtime["runtime_arn"],
                "region": REGION,
            },
            f,
            indent=2,
        )

    print("\n✓ Deployment complete! Test with: python invoke.py")


if __name__ == "__main__":
    main()
