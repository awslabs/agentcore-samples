"""
Deploy the HR Assistant agent to Amazon Bedrock AgentCore Runtime.

Deploys v1 (baseline with config bundle hook) or v2 (adds escalate_to_hr_manager
tool and improved system prompt) for use in optimization A/B testing demos.

Usage:
    python deploy.py --name HRAssistV1 [--region us-east-1] [--version v1]
    python deploy.py --name HRAssistV2 --version v2

Options:
    --name     Runtime name (alphanumeric, used as resource name prefix). Required.
    --region   AWS region (default: us-east-1).
    --version  Agent version: v1 (default) or v2.
               v2 adds "escalate_to_hr_manager" tool and an improved baked-in
               system prompt, simulating a code-level change for target-based
               routing demos.

Output:
    Writes agent_state_{name}.json with:
      runtime_id, runtime_arn, log_group, service_name, role_arn, region
    This state file is loaded by optimize.py and invoke.py.

Prerequisites:
    - AWS CLI configured with credentials
    - pip (for building the ARM64 deployment package)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3

# ── Parse arguments ───────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Deploy HR Assistant to AgentCore Runtime")
parser.add_argument("--name", required=True, help="Runtime name (alphanumeric)")
parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
parser.add_argument(
    "--version",
    default="v1",
    choices=["v1", "v2"],
    help="Agent version: v1=baseline, v2=enhanced (extra tool + improved prompt)",
)
args = parser.parse_args()

RUNTIME_NAME = args.name
REGION = args.region
VERSION = args.version

# ── AWS clients ───────────────────────────────────────────────────────────

sts = boto3.client("sts", region_name=REGION)
ACCOUNT_ID = sts.get_caller_identity()["Account"]

iam = boto3.client("iam", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)

ROLE_NAME = f"{RUNTIME_NAME}Role"
S3_BUCKET = f"bedrock-agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_KEY = f"{RUNTIME_NAME}/deployment_package.zip"
BUILD_DIR = Path(f"/tmp/{RUNTIME_NAME}_build")  # nosec B108
STATE_FILE = Path(f"agent_state_{RUNTIME_NAME}.json")

SCRIPT_DIR = Path(__file__).parent
V1_CODE_PATH = SCRIPT_DIR / "utils" / "hr_assistant_agent.py"

print(f"Deploying {RUNTIME_NAME} (version={VERSION}) to {REGION} (account={ACCOUNT_ID})")

# ── IAM role ──────────────────────────────────────────────────────────────

TRUST_POLICY = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "aws:SourceAccount": ACCOUNT_ID,
                    },
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:*:{ACCOUNT_ID}:*",
                    },
                },
            }
        ],
    }
)

PERMISSIONS_POLICY = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:*",
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeIndexPolicies",
                    "logs:PutIndexPolicy",
                    "logs:FilterLogEvents",
                    "logs:GetLogEvents",
                    "logs:StartQuery",
                    "logs:GetQueryResults",
                    "logs:StopQuery",
                    "cloudwatch:*",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "sts:AssumeRole",
                    "s3:GetObject",
                    "s3:ListBucket",
                ],
                "Resource": "*",
            }
        ],
    }
)

try:
    resp = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=TRUST_POLICY)
    ROLE_ARN = resp["Role"]["Arn"]
    print(f"Created IAM role: {ROLE_ARN}")
except iam.exceptions.EntityAlreadyExistsException:
    ROLE_ARN = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
    print(f"IAM role exists: {ROLE_ARN}")

iam.put_role_policy(
    RoleName=ROLE_NAME,
    PolicyName=f"{RUNTIME_NAME}Policy",
    PolicyDocument=PERMISSIONS_POLICY,
)
print("IAM policy attached. Waiting 10s for propagation...")
time.sleep(10)

# ── Agent code ────────────────────────────────────────────────────────────

# v2 adds an escalation tool and a more detailed system prompt baked into the code.
# This represents a new code deployment (not just a prompt config change).
V2_EXTRA_CODE = '''

# ---------------------------------------------------------------------------
# v2 enhancement: escalate to HR manager (new tool added in this code version)
# ---------------------------------------------------------------------------

@tool
def escalate_to_hr_manager(employee_id: str, issue: str) -> dict:
    """
    Escalate a complex HR issue to a human HR manager for review.

    Args:
        employee_id: Employee identifier involved in the escalation.
        issue:       Brief description of the issue requiring human review.
                     Use this tool when: policy conflicts arise, unusual circumstances
                     need manager judgement, or an employee requests human review.

    Returns:
        Dict with ticket_id, assigned_manager, and expected_response_time.
    """
    import uuid as _uuid
    ticket_id = f"ESC-{_uuid.uuid4().hex[:8].upper()}"
    return {
        "ticket_id": ticket_id,
        "employee_id": employee_id,
        "issue": issue,
        "assigned_manager": "HR Manager (on-call)",
        "expected_response_time": "Within 1 business day",
        "status": "OPEN",
        "message": (
            f"Escalation ticket {ticket_id} created. "
            "An HR manager will review and contact the employee within 1 business day."
        ),
    }
'''

V2_SYSTEM_PROMPT = """You are a knowledgeable and empathetic HR Assistant for Acme Corp (v2).

You assist employees with HR matters through a structured, step-by-step approach:

1. UNDERSTAND the employee's request fully before acting
2. RETRIEVE accurate data using the appropriate tool — never guess or fabricate
3. PRESENT information clearly with specific numbers and dates
4. OFFER next steps proactively (e.g., after showing PTO balance, offer to submit a request)
5. ESCALATE to an HR manager when issues are complex, involve policy exceptions,
   or when the employee explicitly requests human review

Available tools and when to use them:
- get_pto_balance: Always call before submitting a PTO request; shows exact remaining days
- submit_pto_request: Requires employee_id, start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
- lookup_hr_policy: Use for pto, remote_work, parental_leave, or code_of_conduct questions
- get_benefits_summary: Use for health, dental, vision, 401k, or life_insurance questions
- get_pay_stub: Requires employee_id and period (YYYY-MM format)
- escalate_to_hr_manager: Use for complex issues requiring human judgement

Response guidelines:
- Be concise and factual; include specific numbers from tool results
- Confirm actions taken (e.g., "I've submitted PTO request PTO-2026-001")
- Anticipate follow-up needs (e.g., "Would you like me to check the remote work policy?")"""


def build_v1_code() -> str:
    return V1_CODE_PATH.read_text()


def build_v2_code() -> str:
    import re

    base = V1_CODE_PATH.read_text()
    new_prompt = f'DEFAULT_SYSTEM_PROMPT = """{V2_SYSTEM_PROMPT}"""\n'
    base = re.sub(
        r'DEFAULT_SYSTEM_PROMPT = """.*?"""\n',
        new_prompt,
        base,
        flags=re.DOTALL,
    )
    base = base.replace(
        "_MODEL = BedrockModel",
        V2_EXTRA_CODE + "\n_MODEL = BedrockModel",
    )
    base = base.replace(
        "    get_pay_stub,\n]",
        "    get_pay_stub,\n    escalate_to_hr_manager,\n]",
    )
    return base


# ── Build deployment package ──────────────────────────────────────────────

if BUILD_DIR.exists():
    shutil.rmtree(BUILD_DIR)
PKG_DIR = BUILD_DIR / "pkg"
PKG_DIR.mkdir(parents=True)

print(f"Installing dependencies for ARM64 into {PKG_DIR}...")
subprocess.run(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "strands-agents[otel]",
        "bedrock-agentcore",
        "aws-opentelemetry-distro",
        "-t",
        str(PKG_DIR),
        "--platform",
        "manylinux2014_aarch64",
        "--only-binary=:all:",
        "--python-version",
        "3.13",
        "--quiet",
    ],
    check=True,
)

agent_code = build_v2_code() if VERSION == "v2" else build_v1_code()
(PKG_DIR / "main.py").write_text(agent_code)
print(f"Agent code ({VERSION}) written to {PKG_DIR}/main.py")

ZIP_PATH = BUILD_DIR / "deployment_package.zip"
with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, _, files in os.walk(PKG_DIR):
        for f in files:
            if f.endswith(".pyc") or "__pycache__" in root:
                continue
            full = Path(root) / f
            zf.write(full, full.relative_to(PKG_DIR))

size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
print(f"Package built: {ZIP_PATH} ({size_mb:.1f} MB)")

# ── Upload to S3 ──────────────────────────────────────────────────────────

try:
    if REGION == "us-east-1":
        s3.create_bucket(Bucket=S3_BUCKET)
    else:
        s3.create_bucket(
            Bucket=S3_BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
    print(f"Created S3 bucket: {S3_BUCKET}")
except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
    print(f"S3 bucket exists: {S3_BUCKET}")

s3.upload_file(str(ZIP_PATH), S3_BUCKET, S3_KEY)
print(f"Uploaded to s3://{S3_BUCKET}/{S3_KEY}")

# ── Create AgentCore Runtime ──────────────────────────────────────────────

resp = ctrl.create_agent_runtime(
    agentRuntimeName=RUNTIME_NAME,
    agentRuntimeArtifact={
        "codeConfiguration": {
            "code": {"s3": {"bucket": S3_BUCKET, "prefix": S3_KEY}},
            "runtime": "PYTHON_3_13",
            "entryPoint": ["opentelemetry-instrument", "main.py"],
        }
    },
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn=ROLE_ARN,
)
RUNTIME_ID = resp["agentRuntimeId"]
print(f"Runtime created: {RUNTIME_ID}. Polling for READY/ACTIVE...")

for i in range(90):
    detail = ctrl.get_agent_runtime(agentRuntimeId=RUNTIME_ID)
    status = detail.get("status", "UNKNOWN")
    print(f"  Poll {i + 1}: {status}")
    if status in ("ACTIVE", "READY"):
        RUNTIME_ARN = detail.get("agentRuntimeArn")
        break
    if "FAILED" in status:
        raise RuntimeError(f"Runtime failed: {detail.get('failureReason')}")
    time.sleep(10)
else:
    raise RuntimeError("Runtime did not become ready within 15 minutes")

LOG_GROUP = f"/aws/bedrock-agentcore/runtimes/{RUNTIME_ID}-DEFAULT"
SERVICE_NAME = f"{RUNTIME_NAME}.DEFAULT"

# ── Save state ────────────────────────────────────────────────────────────

state = {
    "runtime_name": RUNTIME_NAME,
    "runtime_id": RUNTIME_ID,
    "runtime_arn": RUNTIME_ARN,
    "log_group": LOG_GROUP,
    "service_name": SERVICE_NAME,
    "role_arn": ROLE_ARN,
    "role_name": ROLE_NAME,
    "s3_bucket": S3_BUCKET,
    "s3_key": S3_KEY,
    "region": REGION,
    "version": VERSION,
    "account_id": ACCOUNT_ID,
}
STATE_FILE.write_text(json.dumps(state, indent=2))
print(f"\nState saved to {STATE_FILE}")
print(json.dumps(state, indent=2))
