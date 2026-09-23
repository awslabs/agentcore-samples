"""
Deploy the VPC example.

Step 1: Deploy CDK infrastructure (VPC, Fargate, security groups)
Step 2: Deploy AgentCore Runtime agent in VPC mode

Prerequisites:
    - Node.js and npm installed
    - AWS CDK bootstrapped: npx cdk bootstrap
    - Docker installed (CDK builds container images)

Usage:
    python deploy.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

import boto3
from boto3.session import Session

# ── Configuration ────────────────────────────────────────────────────────────

AGENT_NAME = "vpc_fargate_agent"
PROTOCOL = "HTTP"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "main.py"
CDK_STACK_NAME = "VpcFargateStack"
CDK_OUTPUTS_FILE = "cdk-outputs.json"

# Agent code to include in the deployment zip
AGENT_DIR = "agent"
AGENT_FILES = ["main.py", "requirements.txt"]

# ── AWS Setup ────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{AGENT_NAME}/code.zip"

print(f"Region:     {REGION}")
print(f"Account:    {ACCOUNT_ID}")
print(f"S3 bucket:  {S3_BUCKET}")


# ── Step 0: Check Prerequisites ─────────────────────────────────────────────


def check_prerequisites():
    """Verify that required tools are installed."""
    print("\nChecking prerequisites...")

    for tool in ["node", "npm", "docker"]:
        if not shutil.which(tool):
            print(f"  ✗ {tool} not found. Please install it first.")
            sys.exit(1)
        print(f"  ✓ {tool} found")


# ── Step 1: Deploy CDK Infrastructure ────────────────────────────────────────


def deploy_cdk():
    """Run npm install (if needed) and cdk deploy to create VPC infrastructure."""
    print("\n" + "─" * 60)
    print("Step 1: Deploy CDK infrastructure")
    print("─" * 60)

    # Install npm dependencies if needed
    if not os.path.isdir("node_modules"):
        print("\n  Running npm install...")
        subprocess.run(["npm", "install"], check=True)
    else:
        print("\n  ✓ node_modules exists, skipping npm install")

    # Deploy CDK stack with outputs file
    print("\n  Running cdk deploy (this may take several minutes)...")
    subprocess.run(
        [
            "npx",
            "cdk",
            "deploy",
            "--outputs-file",
            CDK_OUTPUTS_FILE,
            "--require-approval",
            "never",
        ],
        check=True,
    )

    # Read CDK outputs
    if not os.path.exists(CDK_OUTPUTS_FILE):
        print(f"  ✗ {CDK_OUTPUTS_FILE} not found after deployment")
        sys.exit(1)

    with open(CDK_OUTPUTS_FILE) as f:
        outputs = json.load(f)

    stack_outputs = outputs.get(CDK_STACK_NAME, {})
    if not stack_outputs:
        print(f"  ✗ No outputs found for stack '{CDK_STACK_NAME}'")
        sys.exit(1)

    # Extract VPC configuration from CDK outputs
    vpc_id = stack_outputs.get("VpcId", "")
    subnets_str = stack_outputs.get("Subnets", "")
    security_group_id = stack_outputs.get("SecurityGroupId", "")
    service_discovery_name = stack_outputs.get("ServiceDiscoveryName", "")

    if not subnets_str or not security_group_id:
        print("  ✗ Missing required CDK outputs (Subnets, SecurityGroupId)")
        sys.exit(1)

    subnet_ids = [s.strip() for s in subnets_str.split(",") if s.strip()]

    print("\n  ✓ CDK deployment complete")
    print(f"    VPC:              {vpc_id}")
    print(f"    Subnets:          {subnet_ids}")
    print(f"    Security Group:   {security_group_id}")
    print(f"    Service DNS:      {service_discovery_name}")

    return {
        "vpc_id": vpc_id,
        "subnet_ids": subnet_ids,
        "security_group_id": security_group_id,
        "service_discovery_name": service_discovery_name,
    }


# ── Step 2: Create IAM Execution Role ────────────────────────────────────────


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


# ── Step 3: Zip and Upload Agent Code to S3 ─────────────────────────────────


def zip_and_upload_code():
    s3 = boto3.client("s3", region_name=REGION)

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

    # Create zip in memory from agent/ directory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_name in AGENT_FILES:
            file_path = os.path.join(AGENT_DIR, file_name)
            if os.path.exists(file_path):
                zf.write(file_path, file_name)
                print(f"  Added: {file_path}")
            else:
                print(f"  Warning: {file_path} not found, skipping")
    zip_buffer.seek(0)

    # Upload to S3
    s3.put_object(Bucket=S3_BUCKET, Key=S3_PREFIX, Body=zip_buffer.getvalue())
    print(f"  ✓ Uploaded to s3://{S3_BUCKET}/{S3_PREFIX}")


# ── Step 4: Create AgentCore Runtime with VPC Mode ──────────────────────────


def create_runtime(role_arn: str, vpc_config: dict) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print(f"\n  Creating AgentCore Runtime '{AGENT_NAME}' in VPC mode...")
    response = control.create_agent_runtime(
        agentRuntimeName=AGENT_NAME,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {
                    "s3": {
                        "bucket": S3_BUCKET,
                        "prefix": S3_PREFIX,
                    }
                },
                "runtime": PYTHON_RUNTIME,
                "entryPoint": [ENTRY_POINT],
            }
        },
        roleArn=role_arn,
        networkConfiguration={
            "networkMode": "VPC",
            "networkModeConfig": {
                "subnets": vpc_config["subnet_ids"],
                "securityGroups": [vpc_config["security_group_id"]],
            },
        },
        protocolConfiguration={"serverProtocol": PROTOCOL},
        environmentVariables={
            "API_URL": vpc_config["service_discovery_name"],
        },
        description="Agent deployed in VPC mode — calls Fargate service via Cloud Map",
    )

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    print(f"  ✓ Runtime created: {runtime_id}")

    # Wait for READY
    print("  Waiting for runtime to be ready...")
    while True:
        status_resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = status_resp["status"]
        print(f"    Status: {status}")

        if status == "READY":
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            reason = status_resp.get("failureReason", "Unknown")
            print(f"  ✗ Runtime creation failed: {reason}")
            sys.exit(1)

        time.sleep(15)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


# ── Step 5: Create Endpoint ──────────────────────────────────────────────────


def create_endpoint(runtime_id: str) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print("\n  Creating endpoint 'default'...")
    response = control.create_agent_runtime_endpoint(
        agentRuntimeId=runtime_id,
        name="default",
    )

    print(f"  ✓ Endpoint created: {response['agentRuntimeEndpointArn']}")

    # Wait for endpoint READY
    print("  Waiting for endpoint to be ready...")
    while True:
        eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
        if eps.get("runtimeEndpoints"):
            ep = eps["runtimeEndpoints"][0]
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
    print(f"Deploying {AGENT_NAME} to AgentCore Runtime (VPC mode)")
    print("=" * 60)

    check_prerequisites()
    vpc_config = deploy_cdk()
    role_arn = create_execution_role()
    zip_and_upload_code()
    runtime = create_runtime(role_arn, vpc_config)
    create_endpoint(runtime["runtime_id"])

    # Save config for invoke.py and cleanup.py
    config = {
        "agent_name": AGENT_NAME,
        "runtime_id": runtime["runtime_id"],
        "runtime_arn": runtime["runtime_arn"],
        "region": REGION,
        "vpc_config": vpc_config,
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
