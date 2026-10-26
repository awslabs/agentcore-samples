"""Deploy the OpenAI Agents HR Assistant to AgentCore Runtime using the bedrock-agentcore SDK.

Packages the agent source and its dependencies into a zip, uploads to S3, creates an
AgentCore Runtime, and polls until READY. Saves connection details to agent_config.json
in this directory for use by evaluate.py.

Usage:
    python deploy.py [--region REGION]

Output:
    agent_config.json  — AGENT_ID, AGENT_ARN, CW_LOG_GROUP, REGION

Deployment steps:
  1. Create an IAM execution role for the runtime
  2. Create an AgentCore Memory resource (conversation history store)
  3. Package openai_hr_assistant.py + ARM64 dependencies into a zip
  4. Upload the zip to S3
  5. Create an AgentCore Runtime via create_agent_runtime (codeConfiguration),
     injecting AGENTCORE_MEMORY_ID as an environment variable
  6. Poll until READY
  7. Write agent_config.json

The runtime uses OpenAI GPT-5.5 on Bedrock via the mantle endpoint's OpenAI
Responses API, authenticated with a Bedrock API key (short-term by default,
minted from the runtime role; long-term via the BEDROCK_API_KEY env var).
The role policy below grants the required bedrock-mantle:CreateInference and
bedrock-mantle:CallWithBearerToken actions (plus bedrock:CallWithBearerToken for
the bedrock-runtime /openai/v1 Chat Completions alternative). GPT-5.5 is served
from us-east-1/us-east-2; the agent calls it cross-region from wherever the
runtime is deployed.

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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).parent
_CONFIG_FILE = _SCRIPT_DIR / "agent_config.json"
_AGENT_FILE = "openai_hr_assistant.py"

# Bundled into the deployment zip (ARM64). openai-agents talks to the Bedrock
# OpenAI-compatible endpoint; aws-bedrock-token-generator mints the bearer token;
# the OpenTelemetry instrumentation is auto-discovered by ADOT at startup.
_PACKAGES = [
    "openai-agents",
    "openai",
    "opentelemetry-instrumentation-openai-agents>=0.61.0",
    "aws-bedrock-token-generator",
    "bedrock-agentcore",
    "aws-opentelemetry-distro",
]

parser = argparse.ArgumentParser(description="Deploy the OpenAI Agents HR Assistant to AgentCore Runtime")
parser.add_argument("--region", default=None, help="AWS region (default: boto3 session region)")
args = parser.parse_args()

REGION = args.region or Session().region_name or "us-west-2"
print(f"Region: {REGION}")

_sts = boto3.client("sts", region_name=REGION)
_ACCOUNT_ID = _sts.get_caller_identity()["Account"]
_iam = boto3.client("iam", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

_AGENT_NAME = f"hr_openai_{uuid.uuid4().hex[:8]}"
_ROLE_NAME = f"{_AGENT_NAME}_role"
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

_POLICY = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:CallWithBearerToken",
                    "bedrock-mantle:CreateInference",
                    "bedrock-mantle:CallWithBearerToken",
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                    "cloudwatch:PutMetricData",
                ],
                "Resource": "*",
            }
        ],
    }
)

print(f"\n[1/5] Creating IAM role '{_ROLE_NAME}' ...")
try:
    _ROLE_ARN = _iam.create_role(RoleName=_ROLE_NAME, AssumeRolePolicyDocument=_TRUST)["Role"]["Arn"]
    print(f"  Created: {_ROLE_ARN}")
except _iam.exceptions.EntityAlreadyExistsException:
    _ROLE_ARN = _iam.get_role(RoleName=_ROLE_NAME)["Role"]["Arn"]
    print(f"  Already exists: {_ROLE_ARN}")

_iam.put_role_policy(
    RoleName=_ROLE_NAME,
    PolicyName=f"{_AGENT_NAME}_policy",
    PolicyDocument=_POLICY,
)
print("  Policy attached. Waiting 10s for IAM propagation ...")
time.sleep(10)

# ---------------------------------------------------------------------------
# 2. Create AgentCore Memory (conversation history store)
# ---------------------------------------------------------------------------

print(f"\n[2/6] Creating AgentCore Memory '{_AGENT_NAME}_memory' ...")
_mem_resp = _ctrl.create_memory(
    name=f"{_AGENT_NAME}_memory",
    description="Short-term conversation memory for the HR Assistant sample",
    eventExpiryDuration=7,
)
MEMORY_ID = _mem_resp["memory"]["id"]
print(f"  Memory ID: {MEMORY_ID}")
for _elapsed in range(0, 300, 10):
    _mstatus = _ctrl.get_memory(memoryId=MEMORY_ID)["memory"]["status"]
    if _mstatus == "ACTIVE":
        break
    if _mstatus == "FAILED":
        raise RuntimeError("Memory creation failed")
    time.sleep(10)
print(f"  Memory status: {_mstatus}")

# ---------------------------------------------------------------------------
# 3. Build deployment package (ARM64)
# ---------------------------------------------------------------------------

print("\n[3/6] Building deployment package ...")
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
        *_PACKAGES,
        "-t",
        str(_PKG),
        "--platform",
        "manylinux2014_aarch64",
        "--only-binary=:all:",
        "--python-version",
        "3.13",
        # This is an isolated --target install; suppress pip's dependency check
        # against the ambient environment (false positives) and version notice.
        "--no-warn-conflicts",
        "--disable-pip-version-check",
        "--quiet",
    ],
    check=True,
)
shutil.copy(_SCRIPT_DIR / _AGENT_FILE, _PKG / _AGENT_FILE)

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

print("\n[4/6] Uploading to S3 ...")
try:
    if REGION == "us-east-1":
        _s3.create_bucket(Bucket=_S3_BUCKET)
    else:
        _s3.create_bucket(
            Bucket=_S3_BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
    print(f"  Created bucket: {_S3_BUCKET}")
except Exception:
    print(f"  Bucket exists: {_S3_BUCKET}")
_s3.upload_file(str(_ZIP), _S3_BUCKET, _S3_KEY)
print(f"  Uploaded: s3://{_S3_BUCKET}/{_S3_KEY}")

# ---------------------------------------------------------------------------
# 4. Create AgentCore Runtime
# ---------------------------------------------------------------------------

print(f"\n[5/6] Creating AgentCore Runtime '{_AGENT_NAME}' ...")
_resp = _ctrl.create_agent_runtime(
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
    environmentVariables={"AGENTCORE_MEMORY_ID": MEMORY_ID},
)
AGENT_ID = _resp["agentRuntimeId"]
print(f"  Runtime ID: {AGENT_ID}")

# ---------------------------------------------------------------------------
# 5. Poll until READY
# ---------------------------------------------------------------------------

print("\n[6/6] Waiting for READY ...")
for _elapsed in range(0, 600, 15):
    _status = _ctrl.get_agent_runtime(agentRuntimeId=AGENT_ID).get("status", "UNKNOWN")
    print(f"  [{_elapsed:>3}s] {_status}")
    if _status in ("READY", "ACTIVE"):
        break
    if "FAILED" in _status:
        raise RuntimeError(f"Deploy failed: {_status}")
    time.sleep(15)
else:
    raise TimeoutError("Agent did not reach READY in 600s")

AGENT_ARN = _ctrl.get_agent_runtime(agentRuntimeId=AGENT_ID)["agentRuntimeArn"]
CW_LOG_GROUP = f"/aws/bedrock-agentcore/runtimes/{AGENT_ID}-DEFAULT"

# ---------------------------------------------------------------------------
# 6. Save agent_config.json
# ---------------------------------------------------------------------------

_config = {
    "agent_id": AGENT_ID,
    "agent_arn": AGENT_ARN,
    "cw_log_group": CW_LOG_GROUP,
    "region": REGION,
    "role_arn": _ROLE_ARN,
    "s3_bucket": _S3_BUCKET,
    "s3_key": _S3_KEY,
    "memory_id": MEMORY_ID,
}
_CONFIG_FILE.write_text(json.dumps(_config, indent=2))

print("\nDeploy complete.")
print(f"  AGENT_ID     : {AGENT_ID}")
print(f"  AGENT_ARN    : {AGENT_ARN}")
print(f"  CW_LOG_GROUP : {CW_LOG_GROUP}")
print(f"  MEMORY_ID    : {MEMORY_ID}")
print(f"  Config saved : {_CONFIG_FILE}")
