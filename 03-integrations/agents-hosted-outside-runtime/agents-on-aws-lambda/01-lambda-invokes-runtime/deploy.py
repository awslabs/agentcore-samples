"""
Deploy the MCP agent to AgentCore Runtime and create a Lambda invoker.

Phase 1 – AgentCore Runtime:
  - IAM execution role for the runtime
  - S3 upload of the agent zip (uv arm64 build)
  - AgentCore Runtime + endpoint

Phase 2 – Lambda:
  - IAM execution role for Lambda
  - Lambda function with ADOT Layer for trace propagation
  - X-Ray active tracing enabled

All resource IDs are saved to runtime_config.json for use by invoke.py / cleanup.py.

Prerequisites:
  - uv installed
  - AWS credentials configured

Usage:
    python deploy.py
    python invoke.py
    python cleanup.py
"""

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

import boto3
from boto3.session import Session

# ── Configuration ──────────────────────────────────────────────────────────────

AGENT_NAME = f"lambda_mcp_agent_{int(time.time()) % 100000}"
PROTOCOL = "HTTP"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "mcp_agent.py"
AGENT_FILES = ["utils/mcp_agent.py"]

LAMBDA_FUNCTION_NAME = f"agentcore-mcp-invoker-{AGENT_NAME}"
LAMBDA_HANDLER = "lambda_handler.lambda_handler"
LAMBDA_TIMEOUT = 300  # seconds
LAMBDA_MEMORY = 512  # MB

# ADOT Lambda Layer ARNs (Python) by region
# Latest ARNs: https://aws-otel.github.io/docs/getting-started/lambda/lambda-python
ADOT_LAYER_ARNS = {
    "us-east-1": "arn:aws:lambda:us-east-1:615299751070:layer:AWSOpenTelemetryDistroPython:18",
    "us-east-2": "arn:aws:lambda:us-east-2:615299751070:layer:AWSOpenTelemetryDistroPython:15",
    "us-west-1": "arn:aws:lambda:us-west-1:615299751070:layer:AWSOpenTelemetryDistroPython:22",
    "us-west-2": "arn:aws:lambda:us-west-2:615299751070:layer:AWSOpenTelemetryDistroPython:22",
    "ap-south-1": "arn:aws:lambda:ap-south-1:615299751070:layer:AWSOpenTelemetryDistroPython:15",
    "ap-northeast-1": "arn:aws:lambda:ap-northeast-1:615299751070:layer:AWSOpenTelemetryDistroPython:15",
    "ap-northeast-2": "arn:aws:lambda:ap-northeast-2:615299751070:layer:AWSOpenTelemetryDistroPython:15",
    "ap-southeast-1": "arn:aws:lambda:ap-southeast-1:615299751070:layer:AWSOpenTelemetryDistroPython:14",
    "ap-southeast-2": "arn:aws:lambda:ap-southeast-2:615299751070:layer:AWSOpenTelemetryDistroPython:15",
    "eu-central-1": "arn:aws:lambda:eu-central-1:615299751070:layer:AWSOpenTelemetryDistroPython:15",
    "eu-west-1": "arn:aws:lambda:eu-west-1:615299751070:layer:AWSOpenTelemetryDistroPython:15",
    "eu-west-2": "arn:aws:lambda:eu-west-2:615299751070:layer:AWSOpenTelemetryDistroPython:15",
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


# ── Phase 1: AgentCore Runtime ─────────────────────────────────────────────────


def create_runtime_role() -> str:
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
            Description=f"Execution role for {AGENT_NAME} AgentCore Runtime",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"  Created runtime IAM role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
        print(f"  Runtime IAM role exists: {role_arn}")

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
        print(f"  Created S3 bucket: {S3_BUCKET}")
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        print(f"  S3 bucket exists: {S3_BUCKET}")

    if os.path.isdir(pkg_dir):
        shutil.rmtree(pkg_dir)
    if os.path.exists(zip_file):
        os.remove(zip_file)

    print("  Installing arm64 dependencies with uv...")
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

    print(f"  Creating AgentCore Runtime '{AGENT_NAME}'...")
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
        description="MCP agent invoked from Lambda via AgentCore Runtime",
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


def create_runtime_endpoint(runtime_id: str) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print("  Creating endpoint 'default'...")
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


# ── Phase 2: Lambda Function ───────────────────────────────────────────────────


def create_lambda_role(runtime_arn: str) -> str:
    iam = boto3.client("iam", region_name=REGION)
    role_name = f"lambda-agentcore-invoker-{AGENT_NAME}"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"Lambda execution role for {LAMBDA_FUNCTION_NAME}",
        )
        lambda_role_arn = resp["Role"]["Arn"]
        print(f"  Created Lambda IAM role: {lambda_role_arn}")
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        lambda_role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
        print(f"  Lambda IAM role exists: {lambda_role_arn}")

    # Attach AWS managed policies
    for policy_arn in [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess",
        "arn:aws:iam::aws:policy/CloudWatchLambdaApplicationSignalsExecutionRolePolicy",
    ]:
        try:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            print(f"  Attached: {policy_arn.split('/')[-1]}")
        except Exception as e:
            if "already attached" not in str(e).lower():
                print(f"  Policy note: {e}")

    # Inline policy: invoke this specific runtime
    inline = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": runtime_arn,
            }
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{AGENT_NAME}-invoke-policy",
        PolicyDocument=json.dumps(inline),
    )
    return lambda_role_arn


def create_lambda_function(lambda_role_arn: str, runtime_arn: str) -> str:
    lambda_client = boto3.client("lambda", region_name=REGION)

    # Build Lambda zip (just the handler — no extra deps, uses Lambda's built-in boto3)
    zip_file = "lambda_deployment.zip"
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write("utils/lambda_handler.py", arcname="lambda_handler.py")

    with open(zip_file, "rb") as f:
        zip_bytes = f.read()
    os.remove(zip_file)

    adot_layer_arn = ADOT_LAYER_ARNS.get(REGION)

    config = {
        "FunctionName": LAMBDA_FUNCTION_NAME,
        "Runtime": "python3.12",
        "Role": lambda_role_arn,
        "Handler": LAMBDA_HANDLER,
        "Code": {"ZipFile": zip_bytes},
        "Description": "Lambda invoker for AgentCore Runtime MCP agent",
        "Timeout": LAMBDA_TIMEOUT,
        "MemorySize": LAMBDA_MEMORY,
        "Environment": {
            "Variables": {
                "RUNTIME_ARN": runtime_arn,
                "AWS_LAMBDA_EXEC_WRAPPER": "/opt/otel-instrument",
            }
        },
        "TracingConfig": {"Mode": "Active"},
    }
    if adot_layer_arn:
        config["Layers"] = [adot_layer_arn]
        print(f"  ADOT Layer: {adot_layer_arn}")
    else:
        print(f"  Warning: No ADOT Layer found for region {REGION}. Trace propagation may be limited.")
        print("  Check https://aws-otel.github.io/docs/getting-started/lambda/lambda-python")

    try:
        resp = lambda_client.create_function(**config)
        function_arn = resp["FunctionArn"]
        print(f"  Created Lambda function: {LAMBDA_FUNCTION_NAME}")
    except lambda_client.exceptions.ResourceConflictException:
        print("  Updating existing Lambda function...")
        lambda_client.update_function_code(FunctionName=LAMBDA_FUNCTION_NAME, ZipFile=zip_bytes)
        time.sleep(2)
        update = {
            "FunctionName": LAMBDA_FUNCTION_NAME,
            "Environment": config["Environment"],
            "TracingConfig": config["TracingConfig"],
            "Timeout": LAMBDA_TIMEOUT,
            "MemorySize": LAMBDA_MEMORY,
        }
        if adot_layer_arn:
            update["Layers"] = [adot_layer_arn]
        lambda_client.update_function_configuration(**update)
        function_arn = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION_NAME)["Configuration"]["FunctionArn"]
        print(f"  Updated Lambda function: {LAMBDA_FUNCTION_NAME}")

    # Wait for function to be active
    print("  Waiting for Lambda function to be active...")
    waiter = lambda_client.get_waiter("function_active_v2")
    waiter.wait(FunctionName=LAMBDA_FUNCTION_NAME)
    print("  Lambda function is active!")
    return function_arn


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("Phase 1: Deploying MCP Agent to AgentCore Runtime")
    print("=" * 60)

    runtime_role_arn = create_runtime_role()
    build_and_upload_package()
    runtime = create_runtime(runtime_role_arn)
    create_runtime_endpoint(runtime["runtime_id"])

    print()
    print("=" * 60)
    print("Phase 2: Creating Lambda Invoker Function")
    print("=" * 60)

    lambda_role_arn = create_lambda_role(runtime["runtime_arn"])
    function_arn = create_lambda_function(lambda_role_arn, runtime["runtime_arn"])

    config = {
        "agent_name": AGENT_NAME,
        "runtime_id": runtime["runtime_id"],
        "runtime_arn": runtime["runtime_arn"],
        "lambda_function_name": LAMBDA_FUNCTION_NAME,
        "lambda_function_arn": function_arn,
        "region": REGION,
        "runtime_role_name": f"agentcore-{AGENT_NAME}-role",
        "lambda_role_name": f"lambda-agentcore-invoker-{AGENT_NAME}",
        "s3_bucket": S3_BUCKET,
        "s3_prefix": S3_PREFIX,
    }
    with open("runtime_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print()
    print("=" * 60)
    print("Deployment complete!")
    print(f"  Runtime ARN:       {runtime['runtime_arn']}")
    print(f"  Lambda ARN:        {function_arn}")
    print("  Config:            runtime_config.json")
    print("\n  Next steps:")
    print("    python invoke.py")
    print("    View traces: CloudWatch → Gen AI Observability")
    print("=" * 60)


if __name__ == "__main__":
    main()
