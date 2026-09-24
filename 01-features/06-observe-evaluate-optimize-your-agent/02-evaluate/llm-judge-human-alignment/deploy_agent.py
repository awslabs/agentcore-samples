"""Deploy the claims assistant agent to Amazon Bedrock AgentCore Runtime.

Packages agent/claims_assistant_agent.py and its ARM64 dependencies into a zip,
uploads it to S3, creates an AgentCore Runtime, and polls until READY. Saves the
connection details to agent_config.json for the numbered workflow scripts.

Usage:
    python deploy_agent.py [--region REGION]

Deployment steps:
  1. Create an IAM execution role for the runtime
  2. Package the agent and ARM64 dependencies
  3. Upload the zip to S3
  4. Create an AgentCore Runtime (codeConfiguration)
  5. Poll until READY and write agent_config.json

See https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/getting-started-custom.html
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

import boto3
from boto3.session import Session
from common import AGENT_CONFIG_FILE, SAMPLE_DIR

parser = argparse.ArgumentParser(description="Deploy the claims assistant agent to AgentCore Runtime")
parser.add_argument("--region", default=None, help="AWS Region (default: boto3 session Region)")
args = parser.parse_args()

REGION = args.region or Session().region_name or "us-east-1"
print(f"Region: {REGION}")

_ACCOUNT_ID = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
_iam = boto3.client("iam", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

_AGENT_FILE = "claims_assistant_agent.py"
_AGENT_NAME = f"claims_assistant_{uuid.uuid4().hex[:8]}"
_ROLE_NAME = f"{_AGENT_NAME}_role"
_POLICY_NAME = f"{_AGENT_NAME}_policy"
_S3_BUCKET = f"bedrock-agentcore-code-{_ACCOUNT_ID}-{REGION}"
_S3_KEY = f"{_AGENT_NAME}/deployment_package.zip"
_BUILD_DIR = Path(f"/tmp/{_AGENT_NAME}_build")  # nosec B108

# ---------------------------------------------------------------------------
# 1. IAM execution role
# ---------------------------------------------------------------------------

_TRUST = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": _ACCOUNT_ID},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:*:{_ACCOUNT_ID}:runtime/*"},
                },
            }
        ],
    }
)

# Execution policy attached to the runtime role:
#   bedrock:InvokeModel*     - call the agent model
#   logs:*                   - write the runtime log group used by AgentCore Observability
#   xray:*                   - emit OTel trace segments
#   cloudwatch:PutMetricData - publish agent metrics
_POLICY = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{_ACCOUNT_ID}:inference-profile/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": f"arn:aws:logs:{REGION}:{_ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*",
            },
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": f"arn:aws:logs:{REGION}:{_ACCOUNT_ID}:log-group:*",
            },
            {
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
                "Effect": "Allow",
                "Action": ["cloudwatch:PutMetricData"],
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
        ],
    }
)

print(f"\n[1/5] Creating IAM role '{_ROLE_NAME}' ...")
_ROLE_ARN = _iam.create_role(RoleName=_ROLE_NAME, AssumeRolePolicyDocument=_TRUST)["Role"]["Arn"]
_iam.put_role_policy(RoleName=_ROLE_NAME, PolicyName=_POLICY_NAME, PolicyDocument=_POLICY)
print(f"  Created: {_ROLE_ARN}")
print("  Waiting 10s for IAM propagation ...")
time.sleep(10)

# ---------------------------------------------------------------------------
# 2. Build deployment package (ARM64)
# ---------------------------------------------------------------------------

print("\n[2/5] Building deployment package ...")
if _BUILD_DIR.exists():
    shutil.rmtree(_BUILD_DIR)
_PKG = _BUILD_DIR / "pkg"
_PKG.mkdir(parents=True)

subprocess.run(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(SAMPLE_DIR / "agent" / "requirements.txt"),
        "-t",
        str(_PKG),
        "--platform",
        "manylinux2014_aarch64",
        "--only-binary=:all:",
        "--python-version",
        "3.13",
        "--quiet",
    ],
    check=True,
)
shutil.copy(SAMPLE_DIR / "agent" / _AGENT_FILE, _PKG / _AGENT_FILE)

_ZIP = _BUILD_DIR / "deployment_package.zip"
with zipfile.ZipFile(_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, _, files in os.walk(_PKG):
        for f in files:
            if f.endswith(".pyc") or "__pycache__" in root:
                continue
            full = Path(root) / f
            zf.write(full, full.relative_to(_PKG))
print(f"  Package: {_ZIP} ({_ZIP.stat().st_size / 1024 / 1024:.1f} MB)")

# ---------------------------------------------------------------------------
# 3. Upload to S3
# ---------------------------------------------------------------------------

print("\n[3/5] Uploading to S3 ...")
try:
    if REGION == "us-east-1":
        _s3.create_bucket(Bucket=_S3_BUCKET)
    else:
        _s3.create_bucket(Bucket=_S3_BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    print(f"  Created bucket: {_S3_BUCKET}")
except (_s3.exceptions.BucketAlreadyOwnedByYou, _s3.exceptions.BucketAlreadyExists):
    print(f"  Bucket exists: {_S3_BUCKET}")
_s3.upload_file(str(_ZIP), _S3_BUCKET, _S3_KEY, ExtraArgs={"ExpectedBucketOwner": _ACCOUNT_ID})
print(f"  Uploaded: s3://{_S3_BUCKET}/{_S3_KEY}")

# ---------------------------------------------------------------------------
# 4. Create AgentCore Runtime
# ---------------------------------------------------------------------------

print(f"\n[4/5] Creating AgentCore Runtime '{_AGENT_NAME}' ...")
AGENT_ID = _ctrl.create_agent_runtime(
    agentRuntimeName=_AGENT_NAME,
    agentRuntimeArtifact={
        "codeConfiguration": {
            "code": {"s3": {"bucket": _S3_BUCKET, "prefix": _S3_KEY}},
            "runtime": "PYTHON_3_13",
            "entryPoint": ["opentelemetry-instrument", _AGENT_FILE],
        }
    },
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn=_ROLE_ARN,
)["agentRuntimeId"]
print(f"  Runtime ID: {AGENT_ID}")

# ---------------------------------------------------------------------------
# 5. Poll until READY and save agent_config.json
# ---------------------------------------------------------------------------

print("\n[5/5] Waiting for READY ...")
for _elapsed in range(0, 600, 15):
    _status = _ctrl.get_agent_runtime(agentRuntimeId=AGENT_ID).get("status", "UNKNOWN")
    print(f"  [{_elapsed:>3}s] {_status}")
    if _status == "READY":
        break
    if "FAILED" in _status:
        raise RuntimeError(f"Deploy failed: {_status}")
    time.sleep(15)
else:
    raise TimeoutError("Agent did not reach READY in 600s")

_config = {
    "agent_id": AGENT_ID,
    "agent_arn": _ctrl.get_agent_runtime(agentRuntimeId=AGENT_ID)["agentRuntimeArn"],
    "cw_log_group": f"/aws/bedrock-agentcore/runtimes/{AGENT_ID}-DEFAULT",
    # AgentCore emits OTel spans under "<agentRuntimeName>.<endpoint>". Batch evaluation
    # requires this value in dataSourceConfig.cloudWatchLogs.serviceNames.
    "otel_service_name": f"{_AGENT_NAME}.DEFAULT",
    "region": REGION,
    "role_name": _ROLE_NAME,
    "policy_name": _POLICY_NAME,
    "s3_bucket": _S3_BUCKET,
    "s3_key": _S3_KEY,
}
AGENT_CONFIG_FILE.write_text(json.dumps(_config, indent=2))

print("\nDeploy complete.")
for key in ("agent_id", "agent_arn", "cw_log_group", "otel_service_name"):
    print(f"  {key:<18}: {_config[key]}")
print(f"  Config saved      : {AGENT_CONFIG_FILE}")
