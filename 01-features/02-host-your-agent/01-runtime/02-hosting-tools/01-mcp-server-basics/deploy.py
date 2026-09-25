"""
Deploy an MCP server to AgentCore Runtime.

Uses direct code deployment (zip to S3) — no Docker required.
Key difference from agent deployment: serverProtocol is 'MCP'.

Requires `uv` and `zip` on PATH (see the Prerequisites in ../../README.md).

Usage:
    python deploy.py
"""

import json
import os
import sys
import time

import boto3
from boto3.session import Session

AGENT_NAME = "basic_mcp_server"
PROTOCOL = "MCP"
PYTHON_RUNTIME = "PYTHON_3_12"
ENTRY_POINT = "mcp_server.py"
CODE_FILES = ["mcp_server.py"]
# Only the server's dependencies get vendored into the zip. requirements.txt also
# carries boto3 for the local scripts, which mcp_server.py never imports.
SERVER_REQUIREMENTS = "requirements-server.txt"
# A deployment is normally a couple of minutes; the ceiling just stops the status
# poll from spinning forever if the runtime never leaves CREATING.
POLL_TIMEOUT_SECONDS = 900

session = Session()
REGION = session.region_name
if not REGION:
    sys.exit(
        "No AWS region configured. AgentCore is regional and there is no default.\n"
        "  export AWS_REGION=us-west-2    # or set `region` in your AWS profile"
    )
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{AGENT_NAME}/code.zip"
CONFIG_FILE = "runtime_config.json"


def save_config(runtime_id: str, runtime_arn: str) -> None:
    """Record the runtime's identity so cleanup.py can always find it.

    Written as soon as the runtime exists, not at the end of a successful deploy:
    agentRuntimeId is the one identifier that cannot be reconstructed from the
    constants in this file, so if we exit before saving it, a created runtime is
    orphaned and cleanup.py has nothing to work from.
    """
    with open(CONFIG_FILE, "w") as f:
        json.dump(
            {
                "agent_name": AGENT_NAME,
                "runtime_id": runtime_id,
                "runtime_arn": runtime_arn,
                "region": REGION,
            },
            f,
            indent=2,
        )


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

    # An MCP tool server does not call Bedrock models, so there is no bedrock:InvokeModel
    # here. Everything below is the runtime's own observability plumbing: without the
    # X-Ray and CloudWatch metric statements the runtime still serves traffic, but it
    # silently emits no traces and no metrics — the spans log stream is created and
    # stays empty, which looks like observability is wired up when it is not.
    # If your tools call AWS services (DynamoDB, S3, ...), add those permissions too.
    #
    # See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html
    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:DescribeLogStreams"],
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
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass
    except s3.exceptions.BucketAlreadyExists:
        # The name is globally unique across all of S3, so this means another account
        # owns it. Uploading would fail with AccessDenied further down; say so here.
        sys.exit(f"S3 bucket {S3_BUCKET} exists in another AWS account. Rename S3_BUCKET and re-run.")

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
            SERVER_REQUIREMENTS,
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
    # The entry point must sit at the root of the archive, alongside the vendored deps.
    for f in CODE_FILES:
        subprocess.run(["zip", zip_file, f], check=True, capture_output=True)

    zip_size = os.path.getsize(zip_file) / (1024 * 1024)
    print(f"  Package: {zip_file} ({zip_size:.1f} MB)")

    s3.upload_file(zip_file, S3_BUCKET, S3_PREFIX)
    print(f"✓ Code uploaded to s3://{S3_BUCKET}/{S3_PREFIX}")

    shutil.rmtree(pkg_dir)
    os.remove(zip_file)


def create_runtime(role_arn: str) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    try:
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
            description="Basic MCP server with math and greeting tools",
        )
    except control.exceptions.ConflictException:
        sys.exit(
            f"A runtime named '{AGENT_NAME}' already exists.\n"
            f"  Run `python cleanup.py` to remove the previous deployment, or change AGENT_NAME."
        )

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    # Save before polling: the runtime exists now, so cleanup.py must be able to find
    # it even if the wait below times out or is interrupted.
    save_config(runtime_id, runtime_arn)

    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while True:
        status_resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = status_resp["status"]
        print(f"  Runtime status: {status}")
        if status == "READY":
            break
        if "FAILED" in status:
            print(f"  ✗ Failed: {status_resp.get('failureReason')}")
            print("  Run `python cleanup.py` to remove the created resources.")
            sys.exit(1)
        if time.time() > deadline:
            print(f"  ✗ Timed out after {POLL_TIMEOUT_SECONDS}s in status {status}.")
            print("  Run `python cleanup.py` to remove the created resources.")
            sys.exit(1)
        time.sleep(15)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


def smoke_test(runtime_arn: str, runtime_id: str) -> None:
    """Call the deployed server once before reporting success.

    AgentCore does not execute the entry point when it creates the runtime, so a
    server that crashes on import still reaches READY. Without this check, deploy.py
    prints "Deployment complete" for a runtime that can never answer a request, and
    the failure only shows up later as an empty result from invoke.py.
    """
    data = boto3.client("bedrock-agentcore", region_name=REGION)
    payload = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}).encode()

    attempts = 5
    last_error = None
    for attempt in range(attempts):
        try:
            response = data.invoke_agent_runtime(
                agentRuntimeArn=runtime_arn,
                payload=payload,
                contentType="application/json",
                accept="application/json, text/event-stream",
            )
            body = json.loads(response["response"].read().decode("utf-8"))
            tools = body["result"]["tools"]
        except KeyError:
            # Either an error envelope or a body that is not a tools/list result.
            last_error = body.get("error", body)
        except Exception as e:  # noqa: BLE001
            # Throttling and a just-created runtime that is not yet invocable are both
            # transient; anything else (AccessDenied on InvokeAgentRuntime, for example)
            # will simply exhaust the attempts and be reported below.
            last_error = f"{type(e).__name__}: {e}"
        else:
            print(f"✓ Smoke test passed — tools: {', '.join(t['name'] for t in tools)}")
            return
        if attempt < attempts - 1:
            time.sleep(5 * (attempt + 1))

    print(f"✗ Runtime reports READY but is not serving MCP: {last_error}")
    print("  This usually means the server failed to start. Check the traceback in:")
    print(f"    /aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT")
    print("  Then fix the cause, run `python cleanup.py`, and deploy again.")
    sys.exit(1)


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"Deploying {AGENT_NAME} (direct code deployment — no Docker required)\n")
    role_arn = create_execution_role()
    zip_and_upload_code()
    runtime = create_runtime(role_arn)
    # No create_agent_runtime_endpoint call: AgentCore provisions a DEFAULT endpoint
    # with the runtime, and that is the one an invoke with no `qualifier` reaches.
    # Creating a second endpoint adds a redundant resource and its own log group.
    smoke_test(runtime["runtime_arn"], runtime["runtime_id"])

    print("\n✓ Deployment complete! Test with: python invoke.py")


if __name__ == "__main__":
    main()
