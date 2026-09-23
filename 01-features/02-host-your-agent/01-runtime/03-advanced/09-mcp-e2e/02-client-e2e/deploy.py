"""
Deploy the MCP client features e2e agent to AgentCore Runtime.

This MCP server demonstrates elicitation and sampling — client-side
MCP capabilities for interactive, stateful sessions.

Usage:
    python deploy.py
"""

import io
import json
import os
import sys
import time
import zipfile
import boto3
from boto3.session import Session

AGENT_NAME = "mcp_client_e2e"
PROTOCOL = "MCP"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "mcp_client_features.py"
DYNAMO_TABLE = "finance_tracker"

session = Session()
REGION = session.region_name
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{AGENT_NAME}/code.zip"


def create_dynamodb_table():
    """Create the DynamoDB table for the finance tracker."""
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    try:
        table = dynamodb.Table(DYNAMO_TABLE)
        table.load()
        print(f"✓ DynamoDB table '{DYNAMO_TABLE}' already exists")
    except dynamodb.meta.client.exceptions.ResourceNotFoundException:
        table = dynamodb.create_table(
            TableName=DYNAMO_TABLE,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        print(f"✓ DynamoDB table '{DYNAMO_TABLE}' created")


def create_execution_role() -> str:
    iam = boto3.client("iam", region_name=REGION)
    role_name = f"agentcore-{AGENT_NAME}-role"
    trust = {
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
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "arn:aws:logs:*:*:*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:PutItem",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                ],
                "Resource": f"arn:aws:dynamodb:{REGION}:{ACCOUNT_ID}:table/{DYNAMO_TABLE}",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
            },
        ],
    }
    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description=f"Execution role for {AGENT_NAME}",
        )
        role_arn = resp["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{AGENT_NAME}-policy",
        PolicyDocument=json.dumps(policy),
    )
    print(f"✓ IAM role: {role_arn}")
    time.sleep(10)
    return role_arn


def zip_and_upload_code():
    import shutil
    import subprocess

    s3 = boto3.client("s3", region_name=REGION)
    pkg_dir = os.path.join(os.path.dirname(__file__), "deployment_package")
    zip_file = os.path.join(os.path.dirname(__file__), "deployment_package.zip")
    req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")
    agents_dir = os.path.join(os.path.dirname(__file__), "agents")
    helpers_dir = os.path.join(os.path.dirname(__file__), "..", "helpers")

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
            req_file,
        ],
        check=True,
    )

    # Copy source files into deployment_package at root level
    for filename in os.listdir(agents_dir):
        if filename.endswith(".py"):
            shutil.copy(os.path.join(agents_dir, filename), os.path.join(pkg_dir, filename))
            print(f"  Added {filename}")
    if os.path.isdir(helpers_dir):
        for filename in os.listdir(helpers_dir):
            if filename.endswith(".py"):
                shutil.copy(os.path.join(helpers_dir, filename), os.path.join(pkg_dir, filename))
                print(f"  Added helper: {filename}")

    print("  Creating deployment zip...")
    subprocess.run(
        ["zip", "-r", os.path.basename(zip_file), os.path.basename(pkg_dir)],
        cwd=os.path.dirname(__file__),
        check=True,
        capture_output=True,
    )
    # Flatten: re-zip from inside pkg_dir so imports work at root level
    zip_buf = io.BytesIO()  # noqa: F841
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(pkg_dir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for f in files:
                full = os.path.join(root, f)
                arcname = os.path.relpath(full, pkg_dir)
                zf.write(full, arcname)

    zip_size = os.path.getsize(zip_file) / (1024 * 1024)
    print(f"  Package: {os.path.basename(zip_file)} ({zip_size:.1f} MB)")

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
        description="MCP client e2e — elicitation and sampling features",
    )
    runtime_id, runtime_arn = response["agentRuntimeId"], response["agentRuntimeArn"]
    while True:
        s = control.get_agent_runtime(agentRuntimeId=runtime_id)
        print(f"  Status: {s['status']}")
        if s["status"] == "READY":
            break
        if "FAILED" in s["status"]:
            print(f"  ✗ Failed: {s.get('failureReason')}")
            sys.exit(1)
        time.sleep(15)
    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


def create_endpoint(runtime_id: str):
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    control.create_agent_runtime_endpoint(agentRuntimeId=runtime_id, name="default")
    while True:
        eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
        if eps.get("runtimeEndpoints") and eps["runtimeEndpoints"][0]["status"] == "READY":
            break
        time.sleep(15)
    print("✓ Endpoint ready")


def main():
    print(f"Deploying {AGENT_NAME}\n")
    create_dynamodb_table()
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
                "dynamo_table": DYNAMO_TABLE,
            },
            f,
            indent=2,
        )
    print("\n✓ Deployment complete! Run: python invoke.py")


if __name__ == "__main__":
    main()
