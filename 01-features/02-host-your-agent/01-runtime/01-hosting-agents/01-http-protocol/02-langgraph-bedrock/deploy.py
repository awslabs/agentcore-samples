"""
Deploy a LangGraph + Bedrock agent to AgentCore New Runtime using direct code deployment.

Steps:
1. Create an IAM execution role with AgentCore permissions
2. Install arm64 dependencies with uv, zip with agent code, upload to S3
3. Create an AgentCore Runtime with codeConfiguration and platformVersion="V2"
4. Wait for READY (minutes on New Runtime), confirm the platform version, create endpoint

Prerequisites:
    - uv installed (https://docs.astral.sh/uv/getting-started/installation/)
    - AWS CLI configured with credentials
    - boto3 >= 1.43.95 (first public release that models platformVersion)

Usage:
    python deploy.py
"""

import json
import os
import shutil
import subprocess
import sys
import time
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from deployment_state import creating, exclusive_operation, reserve

# ── Configuration ────────────────────────────────────────────────────────────

AGENT_NAME = f"langgraph_bedrock_{uuid4().hex[:20]}"
PROTOCOL = "HTTP"

# AgentCore Runtime platform version. "V2" prepares the execution environment
# once, at create/update time, and snapshots it; new execution environments
# resume from that snapshot instead of loading your code on every cold start.
# This field must be set explicitly — omitting it does not give you New Runtime.
PLATFORM_VERSION = "V2"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "agent.py"

# Agent source files to include in the zip (alongside pre-compiled deps)
AGENT_FILES = ["agent.py"]

# ── Step 1: Create IAM Execution Role ────────────────────────────────────────


def create_execution_role(state) -> str:
    """Create the IAM execution role with the permissions AgentCore needs.

    Uses the official 'AgentCore direct deploy execution role' policy.
    See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html
    """
    REGION, ACCOUNT_ID = state["region"], state["account_id"]
    iam = boto3.client("iam", region_name=REGION)
    role_name = f"agentcore-{AGENT_NAME}-role"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/{AGENT_NAME}-*"
                    },
                },
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
                "Sid": "ReadDeploymentZip",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                "Resource": f"arn:aws:s3:::{state['bucket']}/{state['key']}",
            },
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                    f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:inference-profile/global.anthropic.claude-haiku-4-5-20251001-v1:0",
                ],
            },
        ],
    }

    with creating(state, "IAM role"):
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"Execution role for {AGENT_NAME}",
        )
        role_arn = resp["Role"]["Arn"]
        state["role_id"] = resp["Role"]["RoleId"]
    print(f"\n✓ Created IAM role: {role_arn}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{AGENT_NAME}-policy",
        PolicyDocument=json.dumps(inline_policy),
    )

    print("  Waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn


# ── Step 2: Build arm64 deployment package and upload to S3 ──────────────────


def build_and_upload_package(state):
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
    REGION, S3_BUCKET, S3_PREFIX = state["region"], state["bucket"], state["key"]
    s3 = boto3.client("s3", region_name=REGION)
    pkg_dir = "deployment_package"
    zip_file = "deployment_package.zip"

    # Create S3 bucket if needed
    try:
        s3.head_bucket(Bucket=S3_BUCKET, ExpectedBucketOwner=state["account_id"])
    except ClientError as error:
        if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
            raise
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=S3_BUCKET)
        else:
            s3.create_bucket(
                Bucket=S3_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
        print(f"\n✓ Created S3 bucket: {S3_BUCKET}")

    if os.path.lexists(pkg_dir) or os.path.lexists(zip_file):
        raise FileExistsError("Move previous deployment_package artifacts aside before building.")

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
    with open(zip_file, "rb") as package, creating(state, "code ZIP"):
        upload = s3.put_object(
            Bucket=S3_BUCKET, Key=S3_PREFIX, Body=package, ExpectedBucketOwner=state["account_id"], IfNoneMatch="*"
        )
        state["etag"] = upload["ETag"]
        if "VersionId" in upload:
            state["version_id"] = upload["VersionId"]
    print("  ✓ Uploaded")

    # Clean up local build artifacts
    shutil.rmtree(pkg_dir)
    os.remove(zip_file)


# ── Step 3: Create AgentCore Runtime ─────────────────────────────────────────


def create_runtime(role_arn: str, state) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=state["region"])

    print(f"\n  Creating AgentCore Runtime '{AGENT_NAME}'...")
    with creating(state, "Runtime"):
        response = control.create_agent_runtime(
            agentRuntimeName=AGENT_NAME,
            agentRuntimeArtifact={
                "codeConfiguration": {
                    "code": {"s3": {"bucket": state["bucket"], "prefix": state["key"]}},
                    "runtime": PYTHON_RUNTIME,
                    "entryPoint": [ENTRY_POINT],
                }
            },
            roleArn=role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            protocolConfiguration={"serverProtocol": PROTOCOL},
            platformVersion=PLATFORM_VERSION,
            description="LangGraph agent with Bedrock model on New Runtime — tutorial example",
        )

        state.update(runtime_id=response["agentRuntimeId"], runtime_arn=response["agentRuntimeArn"])

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    print(f"  ✓ Runtime created: {runtime_id}")

    # On New Runtime the snapshot is prepared during create, so this wait is measured in
    # minutes rather than seconds. The loop below has no timeout, so it simply
    # waits; budget accordingly in any automation that wraps this script.
    print("  Waiting for runtime to be ready (New Runtime prepares a snapshot — expect minutes)...")
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

    # create_agent_runtime does not echo platformVersion back, so read it from
    # get_agent_runtime. The service returns the persisted value verbatim with no
    # default, so an absent field means "unknown" rather than a specific version.
    reported = control.get_agent_runtime(agentRuntimeId=runtime_id).get("platformVersion")
    if reported == PLATFORM_VERSION:
        print(f"  ✓ Confirmed platformVersion={reported}")
    elif reported is None:
        print("  ! Service did not report platformVersion; could not confirm from the API")
    else:
        print(f"  ✗ Requested platformVersion={PLATFORM_VERSION} but service reports {reported}")
        sys.exit(1)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


# ── Step 4: Create Endpoint ──────────────────────────────────────────────────


def create_endpoint(runtime_id: str, state) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=state["region"])

    print("\n  Creating endpoint 'default'...")
    with creating(state, "endpoint"):
        response = control.create_agent_runtime_endpoint(
            agentRuntimeId=runtime_id,
            name="default",
        )
        state["endpoint_created_at"] = response["createdAt"].isoformat()
    print(f"  ✓ Endpoint created: {response['agentRuntimeEndpointArn']}")

    print("  Waiting for endpoint to be ready (also longer on New Runtime)...")
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


@exclusive_operation()
def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print("=" * 60)
    print(f"Deploying {AGENT_NAME} to AgentCore Runtime")
    print("  (direct code deployment — no Docker required)")
    print("=" * 60)

    session = boto3.Session()
    region = session.region_name
    if not region:
        raise SystemExit("Select an AWS Region before deploying.")
    account = session.client("sts").get_caller_identity()["Account"]
    state = {
        "agent_name": AGENT_NAME,
        "account_id": account,
        "region": region,
        "bucket": f"agentcore-code-{account}-{region}",
        "key": f"{AGENT_NAME}/code.zip",
    }
    reserve(state)
    role_arn = create_execution_role(state)
    build_and_upload_package(state)
    runtime = create_runtime(role_arn, state)
    create_endpoint(runtime["runtime_id"], state)

    print("\n" + "=" * 60)
    print("✓ Deployment complete!")
    print(f"  Runtime ARN: {runtime['runtime_arn']}")
    print("  Config saved to: runtime_config.json")
    print("\n  Test with: python invoke.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
