"""Deploy the HR Assistant agent to AgentCore Runtime using the bedrock-agentcore SDK.

Packages the agent source and its dependencies into a zip, uploads to S3, creates an
AgentCore Runtime, and polls until READY. Saves connection details to agent_config.json
in this directory for use by evaluation scripts in sibling folders.

Usage:
    python deploy.py [--region REGION]
    python deploy.py --skills-dir ../skills-evaluation/skills \
        --config-output ../skills-evaluation/agent_config.json [--region REGION]

Output:
    utils/agent_config.json by default, or the path supplied with --config-output.

Deployment steps:
  1. Create an IAM execution role for the runtime
  2. Package hr_assistant_agent.py, optional Agent Skills, and ARM64 dependencies
  3. Upload the zip to S3
  4. Create an AgentCore Runtime via create_agent_runtime (codeConfiguration)
  5. Poll until READY
  6. Write the deployment configuration

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
_DEFAULT_CONFIG_FILE = _SCRIPT_DIR / "agent_config.json"

parser = argparse.ArgumentParser(description="Deploy the HR Assistant agent to AgentCore Runtime")
parser.add_argument("--region", default=None, help="AWS region (default: boto3 session region)")
parser.add_argument(
    "--skills-dir",
    type=Path,
    default=None,
    help="Optional directory containing */SKILL.md files to package and enable",
)
parser.add_argument(
    "--config-output",
    type=Path,
    default=None,
    help="Config output path (default: utils/agent_config.json)",
)
args = parser.parse_args()

_CONFIG_FILE = (args.config_output or _DEFAULT_CONFIG_FILE).expanduser().resolve()
_SKILLS_DIR = args.skills_dir.expanduser().resolve() if args.skills_dir else None
_SKILL_FILES = sorted(_SKILLS_DIR.glob("*/SKILL.md")) if _SKILLS_DIR and _SKILLS_DIR.is_dir() else []
if _SKILLS_DIR and not _SKILL_FILES:
    parser.error(f"--skills-dir must contain at least one */SKILL.md file: {_SKILLS_DIR}")
_SKILL_NAMES = [skill_file.parent.name for skill_file in _SKILL_FILES]

REGION = args.region or Session().region_name or "us-east-1"
print(f"Region: {REGION}")
if _SKILLS_DIR:
    print(f"Skills: {', '.join(_SKILL_NAMES)}")

_sts = boto3.client("sts", region_name=REGION)
_ACCOUNT_ID = _sts.get_caller_identity()["Account"]
_iam = boto3.client("iam", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

_AGENT_PREFIX = "hr_skills_assistant" if _SKILLS_DIR else "hr_assistant"
_AGENT_NAME = f"{_AGENT_PREFIX}_{uuid.uuid4().hex[:8]}"
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

# Execution policy attached to the runtime's IAM role:
#   bedrock:InvokeModel / InvokeModelWithResponseStream — call the Nova Lite model
#   logs:*            — write the unified runtime log group used by AgentCore observability
#   xray:*            — emit OTel trace segments for AgentCore spans
#   cloudwatch:PutMetricData — publish agent metrics to CloudWatch
_POLICY = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
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
    PolicyName=_POLICY_NAME,
    PolicyDocument=_POLICY,
)
print("  Policy attached. Waiting 10s for IAM propagation ...")
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
        "strands-agents[otel]",
        "strands-agents-tools",
        "bedrock-agentcore",
        "aws-opentelemetry-distro",
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
shutil.copy(_SCRIPT_DIR / "hr_assistant_agent.py", _PKG / "hr_assistant_agent.py")
if _SKILLS_DIR:
    shutil.copytree(_SKILLS_DIR, _PKG / "skills")
    print(f"  Packaged {len(_SKILL_FILES)} skill(s)")

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
        _s3.create_bucket(
            Bucket=_S3_BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
    print(f"  Created bucket: {_S3_BUCKET}")
except Exception:  # noqa: BLE001 — bucket may already exist; proceed with upload
    print(f"  Bucket exists: {_S3_BUCKET}")
_s3.upload_file(str(_ZIP), _S3_BUCKET, _S3_KEY)
print(f"  Uploaded: s3://{_S3_BUCKET}/{_S3_KEY}")

# ---------------------------------------------------------------------------
# 4. Create AgentCore Runtime
# ---------------------------------------------------------------------------

print(f"\n[4/5] Creating AgentCore Runtime '{_AGENT_NAME}' ...")
_resp = _ctrl.create_agent_runtime(
    agentRuntimeName=_AGENT_NAME,
    agentRuntimeArtifact={
        "codeConfiguration": {
            "code": {"s3": {"bucket": _S3_BUCKET, "prefix": _S3_KEY}},
            "runtime": "PYTHON_3_13",
            "entryPoint": ["opentelemetry-instrument", "hr_assistant_agent.py"],
        }
    },
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn=_ROLE_ARN,
)
AGENT_ID = _resp["agentRuntimeId"]
print(f"  Runtime ID: {AGENT_ID}")

# ---------------------------------------------------------------------------
# 5. Poll until READY
# ---------------------------------------------------------------------------

print("\n[5/5] Waiting for READY ...")
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
# AgentCore emits OTel spans under the service name "<agentRuntimeName>.<endpoint>".
# start_batch_evaluation requires this in dataSourceConfig.cloudWatchLogs.serviceNames.
OTEL_SERVICE_NAME = f"{_AGENT_NAME}.DEFAULT"

# ---------------------------------------------------------------------------
# 6. Save agent_config.json
# ---------------------------------------------------------------------------

_config = {
    "agent_id": AGENT_ID,
    "agent_arn": AGENT_ARN,
    "cw_log_group": CW_LOG_GROUP,
    "otel_service_name": OTEL_SERVICE_NAME,
    "region": REGION,
    "role_arn": _ROLE_ARN,
    "role_name": _ROLE_NAME,
    "policy_name": _POLICY_NAME,
    "s3_bucket": _S3_BUCKET,
    "s3_key": _S3_KEY,
    "skills_enabled": bool(_SKILLS_DIR),
    "skills": _SKILL_NAMES,
}
_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
_CONFIG_FILE.write_text(json.dumps(_config, indent=2))

print("\nDeploy complete.")
print(f"  AGENT_ID     : {AGENT_ID}")
print(f"  AGENT_ARN    : {AGENT_ARN}")
print(f"  CW_LOG_GROUP : {CW_LOG_GROUP}")
print(f"  OTel Service : {OTEL_SERVICE_NAME}")
print(f"  Config saved : {_CONFIG_FILE}")
