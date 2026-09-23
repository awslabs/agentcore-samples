# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3"]
# ///
"""
Deploy the banking-tools and portfolio-tools Lambda functions and grant
AgentCore Gateway invoke permission. The script is idempotent; re-running
it safely skips steps that already completed.

Usage:
    uv run deploy_lambda.py
"""

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROLE_NAME = "banking-tools-lambda-role"

FUNCTIONS = [
    {
        "name": "banking-tools",
        "handler_path": Path(__file__).parent / "banking-tools/handler.py",
    },
    {
        "name": "portfolio-tools",
        "handler_path": Path(__file__).parent / "portfolio-tools/handler.py",
    },
]


def get_account_id(session: boto3.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


def ensure_role(iam) -> str:
    trust = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )
    try:
        role = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=trust)
        arn = role["Role"]["Arn"]
        print(f"  Created IAM role: {arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"  IAM role already exists: {arn}")

    try:
        iam.attach_role_policy(
            RoleName=ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        print("  Attached AWSLambdaBasicExecutionRole")
    except ClientError as e:
        if "already attached" not in str(e).lower():
            raise
        print("  AWSLambdaBasicExecutionRole already attached")

    return arn


def build_zip(handler_path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(handler_path, arcname="handler.py")
    return buf.getvalue()


def ensure_lambda(lam, function_name: str, handler_path: Path, role_arn: str) -> str:
    code = build_zip(handler_path)
    try:
        resp = lam.create_function(
            FunctionName=function_name,
            Runtime="python3.14",
            Role=role_arn,
            Handler="handler.lambda_handler",
            Code={"ZipFile": code},
            Timeout=30,
        )
        arn = resp["FunctionArn"]
        print(f"  Created Lambda function: {arn}")
        print("  Waiting for function to become active...", end="", flush=True)
        lam.get_waiter("function_active_v2").wait(FunctionName=function_name)
        print(" ready")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise
        arn = lam.get_function(FunctionName=function_name)["Configuration"][
            "FunctionArn"
        ]
        print(f"  Lambda function already exists: {arn}")
        # Function already exists — push the latest handler code so re-runs
        # actually deploy local changes instead of silently skipping.
        lam.update_function_code(FunctionName=function_name, ZipFile=code)
        print("  Updating function code...", end="", flush=True)
        lam.get_waiter("function_updated_v2").wait(FunctionName=function_name)
        print(" updated")

    return arn


def ensure_permission(lam, function_name: str):
    try:
        lam.add_permission(
            FunctionName=function_name,
            StatementId="AllowAgentCoreGateway",
            Action="lambda:InvokeFunction",
            Principal="bedrock-agentcore.amazonaws.com",
        )
        print("  Granted bedrock-agentcore.amazonaws.com invoke permission")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise
        print("  Invoke permission already exists")


def main():
    for fn in FUNCTIONS:
        if not fn["handler_path"].exists():
            print(f"Error: handler not found at {fn['handler_path']}", file=sys.stderr)
            sys.exit(1)

    session = boto3.Session()
    region = session.region_name or "us-east-1"
    get_account_id(session)
    iam = session.client("iam")
    lam = session.client("lambda", region_name=region)

    print("Step 1: IAM role")
    role_arn = ensure_role(iam)
    print("  Waiting for role propagation...")
    time.sleep(10)

    arns = {}
    for i, fn in enumerate(FUNCTIONS, start=2):
        print(f"\nStep {i}: {fn['name']} Lambda")
        arns[fn["name"]] = ensure_lambda(lam, fn["name"], fn["handler_path"], role_arn)
        print(f"  Invoke permission for {fn['name']}")
        ensure_permission(lam, fn["name"])

    print("\nDone.")
    for name, arn in arns.items():
        print(f"  {name}: {arn}")


if __name__ == "__main__":
    main()
