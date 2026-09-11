"""
End-to-end deploy for the MicroVM + AgentCore Observability demo.

Idempotent: safe to run multiple times. Each step checks whether the
resource already exists and either reuses it or creates it.

Steps:
  1. Enable CloudWatch Transaction Search (if not already; one-time per account).
  2. Create the agent's log group + attach a Logs resource policy so X-Ray can
     deliver spans into it.
  3. Create the S3 bucket for the code artifact.
  4. Create the build IAM role (trusted by lambda.amazonaws.com).
  5. Create the execution IAM role (trusted by lambda.amazonaws.com,
     permissions for bedrock + logs + xray + cloudwatch).
  6. Package app/ into app.zip (Dockerfile at the archive root).
  7. Upload to S3.
  8. Create (or update) the MicroVM image with all OTEL env vars baked in.
  9. Poll GetMicrovmImage until state is CREATED (or CREATE_FAILED).

Run:
    python3 scripts/deploy.py
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import time
import zipfile
from typing import Any

import boto3
import botocore.exceptions

# Make sibling imports work whether run as a module or a script.
HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import config

# --------------------------------------------------------------------------
# Small logging helpers so the operator can follow along
# --------------------------------------------------------------------------

def step(msg: str) -> None:
    print(f"\n\033[1;36m▶ {msg}\033[0m")

def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")

def info(msg: str) -> None:
    print(f"  · {msg}")

def warn(msg: str) -> None:
    print(f"  \033[33m!\033[0m {msg}")


# --------------------------------------------------------------------------
# Step 1: CloudWatch Transaction Search
# --------------------------------------------------------------------------

def enable_transaction_search() -> None:
    step("1/9 CloudWatch Transaction Search")
    xray = boto3.client("xray", region_name=config.REGION)
    try:
        dest = xray.get_trace_segment_destination()
        if dest.get("Destination") == "CloudWatchLogs" and dest.get("Status") == "ACTIVE":
            ok("Transaction Search already ACTIVE (CloudWatchLogs)")
            return
        info(f"current: {dest.get('Destination')}/{dest.get('Status')}")
    except botocore.exceptions.ClientError as e:
        info(f"get_trace_segment_destination: {e.response['Error']['Code']}")

    info("switching X-Ray trace segment destination to CloudWatchLogs …")
    xray.update_trace_segment_destination(Destination="CloudWatchLogs")
    ok("Transaction Search enabled")


# --------------------------------------------------------------------------
# Step 2: log group + X-Ray resource policy for the agent's log group
# --------------------------------------------------------------------------

def ensure_log_group_and_policy() -> None:
    step("2/9 Agent log group + X-Ray resource policy")
    logs = boto3.client("logs", region_name=config.REGION)
    try:
        logs.create_log_group(logGroupName=config.AGENT_LOG_GROUP)
        ok(f"created log group {config.AGENT_LOG_GROUP}")
    except logs.exceptions.ResourceAlreadyExistsException:
        ok(f"log group already exists: {config.AGENT_LOG_GROUP}")

    # Pre-create the log streams the ADOT OTLP exporter targets. The
    # CloudWatch OTLP endpoint requires the stream to exist — it does not
    # auto-create — so a missing stream shows up in the agent log as
    # "The specified log stream does not exist" 400s until we create it.
    for stream in (config.AGENT_LOG_STREAM_RUNTIME, config.AGENT_LOG_STREAM_SPANS):
        try:
            logs.create_log_stream(
                logGroupName=config.AGENT_LOG_GROUP, logStreamName=stream,
            )
            ok(f"created log stream {stream}")
        except logs.exceptions.ResourceAlreadyExistsException:
            ok(f"log stream already exists: {stream}")

    # The shared aws/spans log group is reserved by AWS and created
    # automatically by Transaction Search. Don't try to create it — the
    # resource policy below covers it anyway.

    acct = config.account_id()
    policy_name = f"{config.PREFIX}-xray-span-delivery"
    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AWSLogsXRaySpanDelivery",
                "Effect": "Allow",
                "Principal": {"Service": "xray.amazonaws.com"},
                "Action": "logs:PutLogEvents",
                "Resource": [
                    config.agent_log_group_arn(),
                    f"arn:aws:logs:{config.REGION}:{acct}:log-group:aws/spans:*",
                ],
                "Condition": {
                    "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{config.REGION}:{acct}:*"},
                    "StringEquals": {"aws:SourceAccount": acct},
                },
            }
        ],
    }
    logs.put_resource_policy(
        policyName=policy_name, policyDocument=json.dumps(policy_doc)
    )
    ok(f"resource policy '{policy_name}' installed")


# --------------------------------------------------------------------------
# Step 3: S3 bucket
# --------------------------------------------------------------------------

def ensure_bucket() -> str:
    step("3/9 S3 bucket for the code artifact")
    bucket = config.bucket_name()
    s3 = boto3.client("s3", region_name=config.REGION)
    try:
        s3.head_bucket(Bucket=bucket)
        ok(f"bucket already exists: {bucket}")
        return bucket
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("404", "NoSuchBucket", "NotFound"):
            # 403 usually means the bucket exists but belongs to another account.
            raise

    kwargs: dict[str, Any] = {"Bucket": bucket}
    if config.REGION != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": config.REGION}
    s3.create_bucket(**kwargs)
    # Block public access by default.
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True,
            "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    ok(f"created bucket: {bucket} (private + AES256)")
    return bucket


# --------------------------------------------------------------------------
# Steps 4-5: IAM roles
# --------------------------------------------------------------------------

TRUST_POLICY_LAMBDA = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": ["sts:AssumeRole", "sts:TagSession"],
        }
    ],
}


def _create_or_get_role(role_name: str, trust: dict, description: str) -> str:
    iam = boto3.client("iam")
    try:
        r = iam.get_role(RoleName=role_name)
        ok(f"role already exists: {role_name}")
        return r["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass
    r = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description=description,
    )
    ok(f"created role: {role_name}")
    return r["Role"]["Arn"]


def _put_inline_policy(role_name: str, policy_name: str, doc: dict) -> None:
    iam = boto3.client("iam")
    iam.put_role_policy(
        RoleName=role_name, PolicyName=policy_name, PolicyDocument=json.dumps(doc)
    )
    ok(f"inline policy '{policy_name}' on {role_name}")


def ensure_build_role(bucket: str) -> str:
    step("4/9 Build role")
    arn = _create_or_get_role(
        config.BUILD_ROLE_NAME,
        TRUST_POLICY_LAMBDA,
        "Lambda assumes this to build the MicroVM image",
    )
    _put_inline_policy(
        config.BUILD_ROLE_NAME,
        "build",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                    "Resource": f"arn:aws:s3:::{bucket}/*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": "*",
                },
            ],
        },
    )
    return arn


def ensure_exec_role() -> str:
    step("5/9 Execution role")
    arn = _create_or_get_role(
        config.EXEC_ROLE_NAME,
        TRUST_POLICY_LAMBDA,
        "MicroVM execution role: Bedrock + CloudWatch OTLP + X-Ray",
    )
    _put_inline_policy(
        config.EXEC_ROLE_NAME,
        "runtime",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "BedrockInvoke",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                        "bedrock:Converse",
                        "bedrock:ConverseStream",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "CloudWatchLogs",
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogStreams",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "XRay",
                    "Effect": "Allow",
                    "Action": [
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "CloudWatchMetrics",
                    "Effect": "Allow",
                    "Action": ["cloudwatch:PutMetricData"],
                    "Resource": "*",
                },
            ],
        },
    )
    # IAM eventual consistency: wait for the trust policy to propagate before
    # Lambda tries to assume the role.
    info("waiting 8s for IAM to propagate …")
    time.sleep(8)
    return arn


# --------------------------------------------------------------------------
# Steps 6-7: package + upload
# --------------------------------------------------------------------------

def package_and_upload(bucket: str) -> str:
    step("6/9 Package app/ into app.zip")
    app_dir = HERE / "app"
    if not app_dir.is_dir():
        raise SystemExit(f"missing app dir: {app_dir}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_dir():
                continue
            if any(p in {"__pycache__", ".venv", ".DS_Store"} for p in path.parts):
                continue
            arcname = path.relative_to(app_dir).as_posix()
            zf.write(path, arcname)
            info(f"+ {arcname}")
    payload = buf.getvalue()
    ok(f"zip built ({len(payload)} bytes)")

    step("7/9 Upload to S3")
    s3 = boto3.client("s3", region_name=config.REGION)
    s3.put_object(
        Bucket=bucket,
        Key=config.CODE_ARTIFACT_KEY,
        Body=payload,
        ContentType="application/zip",
        ServerSideEncryption="AES256",
    )
    uri = f"s3://{bucket}/{config.CODE_ARTIFACT_KEY}"
    ok(f"uploaded {uri}")
    return uri


# --------------------------------------------------------------------------
# Step 8: create (or update) the MicroVM image
# --------------------------------------------------------------------------

def create_or_update_image(code_uri: str, build_role: str) -> str:
    step("8/9 Create MicroVM image")
    mvm = boto3.client("lambda-microvms", region_name=config.REGION)

    envs = config.otel_env()
    info(f"baking {len(envs)} OTEL env vars into the image")

    # Hooks are ENABLED/DISABLED toggles — the HTTP paths themselves are fixed
    # by Lambda (/aws/lambda-microvms/runtime/v1/ready and /validate). The
    # port here is where our app listens for the hook requests.
    kwargs = {
        "name": config.IMAGE_NAME,
        "codeArtifact": {"uri": code_uri},
        "baseImageArn": config.DEFAULT_BASE_IMAGE_ARN,
        "buildRoleArn": build_role,
        "description": "Strands agent + ADOT SDK -> AgentCore Observability",
        "cpuConfigurations": [{"architecture": "ARM_64"}],
        "resources": [{"minimumMemoryInMiB": config.BASELINE_MEMORY_MIB}],
        "environmentVariables": envs,
        "logging": {"cloudWatch": {"logGroup": config.BUILD_LOG_GROUP}},
        "hooks": {
            "port": 8080,
            "microvmImageHooks": {
                "ready": "ENABLED",
                "readyTimeoutInSeconds": 300,
                "validate": "ENABLED",
                "validateTimeoutInSeconds": 120,
            },
        },
        "tags": {"project": config.PREFIX},
    }

    try:
        r = mvm.create_microvm_image(**kwargs)
        ok(f"create_microvm_image accepted (state={r['state']})")
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("ConflictException", "ResourceConflictException"):
            info("image already exists — triggering update_microvm_image")
            r = mvm.update_microvm_image(
                imageIdentifier=config.IMAGE_NAME,
                codeArtifact={"uri": code_uri},
                baseImageArn=config.DEFAULT_BASE_IMAGE_ARN,
                buildRoleArn=build_role,
                description="Strands agent + ADOT SDK -> AgentCore Observability",
                cpuConfigurations=[{"architecture": "ARM_64"}],
                resources=[{"minimumMemoryInMiB": config.BASELINE_MEMORY_MIB}],
                environmentVariables=envs,
                logging={"cloudWatch": {"logGroup": config.BUILD_LOG_GROUP}},
                hooks=kwargs["hooks"],
            )
            ok(f"update_microvm_image accepted (state={r['state']})")
        else:
            raise
    return config.image_arn()


def wait_image(image_id: str) -> None:
    step("9/9 Poll GetMicrovmImage until CREATED")
    mvm = boto3.client("lambda-microvms", region_name=config.REGION)
    logs_url = (
        f"https://console.aws.amazon.com/cloudwatch/home?region={config.REGION}"
        f"#logsV2:log-groups/log-group/{config.BUILD_LOG_GROUP.replace('/', '$252F')}"
    )
    info(f"build log group: {config.BUILD_LOG_GROUP}")
    info(f"  console: {logs_url}")
    last = None
    started = time.time()
    while True:
        r = mvm.get_microvm_image(imageIdentifier=image_id)
        state = r["state"]
        version = r.get("latestActiveImageVersion") or r.get("latestFailedImageVersion") or "-"
        elapsed = int(time.time() - started)
        if state != last:
            info(f"[{elapsed:>4}s] state={state} version={version}")
            last = state
        if state in ("CREATED", "UPDATED"):
            ok(f"image is {state} (version {version})")
            return
        if state in ("CREATION_FAILED", "UPDATE_FAILED"):
            raise SystemExit(f"image build failed: {state}. Check {logs_url}")
        time.sleep(15)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    print("\033[1mMicroVM + AgentCore Observability demo — deploy\033[0m")
    print(f"account={config.account_id()}  region={config.REGION}  prefix={config.PREFIX}")

    enable_transaction_search()
    ensure_log_group_and_policy()
    bucket = ensure_bucket()
    build_role = ensure_build_role(bucket)
    ensure_exec_role()
    code_uri = package_and_upload(bucket)
    image_id = create_or_update_image(code_uri, build_role)
    wait_image(image_id)

    print("\n\033[1;32mDeploy complete.\033[0m")
    print(f"  image ARN: {image_id}")
    print("  next:      python3 invoke.py")


if __name__ == "__main__":
    main()
