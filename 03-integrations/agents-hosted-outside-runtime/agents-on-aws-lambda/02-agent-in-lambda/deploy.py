"""
Deploy a Strands agent as an AWS Lambda function with ADOT observability.

Creates:
  - IAM execution role for Lambda
  - Lambda function (Python 3.13, x86_64) with strands-agents bundled
  - ADOT managed layer for Gen AI span export
  - X-Ray active tracing + Application Signals enabled

All resource identifiers are saved to lambda_config.json.

Usage:
    python deploy.py
    python invoke.py
    python evaluate.py
    python cleanup.py
"""

import json
import os
import shutil
import subprocess
import time

import boto3
from boto3.session import Session

# ── Configuration ──────────────────────────────────────────────────────────────

FUNCTION_NAME = f"strands-lambda-obs-{int(time.time()) % 100000}"
HANDLER = "lambda_agent.handler"
TIMEOUT = 300  # seconds – LLM calls can be slow
MEMORY = 512  # MB

# ADOT Lambda layer ARNs — AWSOpenTelemetryDistroPython (x86_64)
# Source: https://aws-otel.github.io/docs/getting-started/lambda/lambda-python
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

# ── AWS setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

print(f"Region:   {REGION}")
print(f"Account:  {ACCOUNT_ID}")
print(f"Function: {FUNCTION_NAME}")


# ── IAM ────────────────────────────────────────────────────────────────────────


def create_lambda_role() -> str:
    iam = boto3.client("iam", region_name=REGION)
    role_name = f"{FUNCTION_NAME}-role"

    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}
        ],
    }
    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description=f"Execution role for {FUNCTION_NAME}",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"  Created IAM role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
        print(f"  IAM role exists:  {role_arn}")

    managed_policies = [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess",
        "arn:aws:iam::aws:policy/AmazonBedrockFullAccess",
        "arn:aws:iam::aws:policy/CloudWatchLambdaApplicationSignalsExecutionRolePolicy",
    ]
    for policy_arn in managed_policies:
        try:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        except Exception as e:
            if "already attached" not in str(e).lower():
                print(f"  Policy note ({policy_arn.split('/')[-1]}): {e}")

    print("  Waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn


# ── Build Lambda ZIP ───────────────────────────────────────────────────────────


def build_zip() -> bytes:
    """Install strands-agents + aws-opentelemetry-distro for Linux x86_64 and zip with lambda_agent.py.

    aws-opentelemetry-distro must be bundled alongside strands-agents so that
    the opentelemetry-sdk version in /var/task is compatible with what the ADOT
    layer's /opt/otel-instrument startup script expects (it imports LogData from
    opentelemetry.sdk._logs).  Without it, strands-agents' bundled older OTel
    packages shadow the layer and cause an ImportError at startup.
    """
    pkg_dir = "_lambda_pkg"
    zip_path = "_lambda_pkg.zip"

    if os.path.isdir(pkg_dir):
        shutil.rmtree(pkg_dir)

    print("  Installing strands-agents for linux/x86_64 with uv...")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            "x86_64-manylinux2014",
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
        capture_output=True,
    )

    print(f"  Creating {zip_path}...")
    subprocess.run(
        ["zip", "-r9q", f"../{zip_path}", "."],
        cwd=pkg_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["zip", "-g", zip_path, "lambda_agent.py"],
        check=True,
        capture_output=True,
    )

    with open(zip_path, "rb") as f:
        data = f.read()

    shutil.rmtree(pkg_dir)
    os.remove(zip_path)
    print(f"  Package: {len(data) / (1024 * 1024):.1f} MB")
    return data


# ── Lambda function ────────────────────────────────────────────────────────────


def deploy_lambda(role_arn: str, zip_bytes: bytes) -> str:
    lam = boto3.client("lambda", region_name=REGION)

    adot_arn = ADOT_LAYER_ARNS.get(REGION)
    if not adot_arn:
        print(f"  WARNING: no ADOT ARN found for {REGION}. Add it to ADOT_LAYER_ARNS.")
        print("  Check https://aws-otel.github.io/docs/getting-started/lambda/lambda-python")

    env_vars = {
        "AGENT_OBSERVABILITY_ENABLED": "true",
        "AWS_LAMBDA_EXEC_WRAPPER": "/opt/otel-instrument",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_AWS_APPLICATION_SIGNALS_ENABLED": "true",
        "OTEL_RESOURCE_ATTRIBUTES": f"service.name={FUNCTION_NAME},service.version=1.0",
    }

    cfg = {
        "FunctionName": FUNCTION_NAME,
        "Runtime": "python3.13",
        "Role": role_arn,
        "Handler": HANDLER,
        "Code": {"ZipFile": zip_bytes},
        "Description": "Strands agent with ADOT observability",
        "Timeout": TIMEOUT,
        "MemorySize": MEMORY,
        "Environment": {"Variables": env_vars},
        "TracingConfig": {"Mode": "Active"},
    }
    if adot_arn:
        cfg["Layers"] = [adot_arn]
        print(f"  ADOT layer: {adot_arn}")

    try:
        resp = lam.create_function(**cfg)
        arn = resp["FunctionArn"]
        print(f"  Created Lambda: {FUNCTION_NAME}")
    except lam.exceptions.ResourceConflictException:
        print("  Updating existing Lambda...")
        lam.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=zip_bytes)
        time.sleep(3)
        upd = {k: cfg[k] for k in ("FunctionName", "Environment", "TracingConfig", "Timeout", "MemorySize")}
        if adot_arn:
            upd["Layers"] = [adot_arn]
        lam.update_function_configuration(**upd)
        arn = lam.get_function(FunctionName=FUNCTION_NAME)["Configuration"]["FunctionArn"]
        print(f"  Updated Lambda: {FUNCTION_NAME}")

    print("  Waiting for Lambda to be active...")
    lam.get_waiter("function_active_v2").wait(FunctionName=FUNCTION_NAME)
    print("  Lambda is active.")
    return arn


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    print("\n" + "=" * 60)
    print("Deploying Strands agent Lambda with ADOT observability")
    print("=" * 60)

    role_arn = create_lambda_role()
    zip_bytes = build_zip()
    function_arn = deploy_lambda(role_arn, zip_bytes)

    config = {
        "function_name": FUNCTION_NAME,
        "function_arn": function_arn,
        "role_name": f"{FUNCTION_NAME}-role",
        "region": REGION,
        "account_id": ACCOUNT_ID,
        "lambda_log_group": f"/aws/lambda/{FUNCTION_NAME}",
        "spans_log_group": "/aws/spans",
        "service_name": FUNCTION_NAME,
    }
    with open("lambda_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print("\n" + "=" * 60)
    print("Deployment complete!")
    print(f"  Function ARN: {function_arn}")
    print("\nNext steps:")
    print("  python invoke.py     # generate 5 sessions")
    print("  python evaluate.py   # run batch evaluation")
    print("  python cleanup.py    # delete all resources")
    print("=" * 60)


if __name__ == "__main__":
    main()
