"""Deploy the HR Assistant agent to AgentCore Runtime using the agentcore CLI.

Run from the notebook via: %run -i deploy_hr_assistant_agent.py

Expects REGION to be set in the caller's namespace (Step 2 config cell).
Sets in the caller's namespace: AGENT_ID, AGENT_ARN, CW_LOG_GROUP, agentcore_client

Uses the agentcore CLI (from bedrock-agentcore) which handles
CodeBuild image builds, ECR push, OTel instrumentation, and runtime creation.
"""

import subprocess
import time
import uuid

import boto3

_REGION = REGION  # noqa: F821
_AGENT_NAME = f"hr_assistant_{uuid.uuid4().hex[:8]}"

# ---- 1. Configure ----
print(f"Configuring agent '{_AGENT_NAME}' ...")
subprocess.run(
    [
        "agentcore",
        "configure",
        "--entrypoint",
        "hr_assistant_agent.py",
        "--name",
        _AGENT_NAME,
        "--region",
        _REGION,
        "--requirements-file",
        "requirements.txt",
        "--non-interactive",
    ],
    check=True,
)
print("Configuration complete.")

# ---- 2. Deploy ----
print("\nDeploying HR Assistant Agent ...")
print("  This takes ~5 minutes on first run (image build + push + runtime creation).")
subprocess.run(
    ["agentcore", "deploy", "--auto-update-on-conflict"],
    check=True,
)
print("Deploy complete.")

# ---- 3. Get AGENT_ID and AGENT_ARN from the control plane ----
print("\nRetrieving agent info ...")
cp = boto3.client("bedrock-agentcore-control", region_name=_REGION)
AGENT_ID = ""
AGENT_ARN = ""
paginator = cp.get_paginator("list_agent_runtimes")
for page in paginator.paginate():
    for rt in page.get("agentRuntimes", []):
        if rt.get("agentRuntimeName") == _AGENT_NAME:
            AGENT_ID = rt["agentRuntimeId"]
            AGENT_ARN = rt["agentRuntimeArn"]
            break
    if AGENT_ID:
        break

if not AGENT_ID:
    raise RuntimeError(f"Could not find {_AGENT_NAME} runtime after deploy")

# ---- 4. Wait for READY ----
if AGENT_ID:
    cp = boto3.client("bedrock-agentcore-control", region_name=_REGION)
    print("Waiting for READY ...")
    for elapsed in range(0, 600, 15):
        status = cp.get_agent_runtime(agentRuntimeId=AGENT_ID).get("status", "UNKNOWN")
        print(f"  [{elapsed:>3}s] {status}")
        if status in ("READY", "ACTIVE"):
            break
        if status in ("FAILED", "CREATE_FAILED", "UPDATE_FAILED"):
            raise RuntimeError(f"Deploy failed: {status}")
        time.sleep(15)
    else:
        raise TimeoutError("Agent did not reach READY in 600s")

CW_LOG_GROUP = f"/aws/bedrock-agentcore/runtimes/{AGENT_ID}-DEFAULT"
agentcore_client = boto3.client("bedrock-agentcore", region_name=_REGION)

print(f"\nAGENT_ID     : {AGENT_ID}")
print(f"AGENT_ARN    : {AGENT_ARN}")
print(f"CW_LOG_GROUP : {CW_LOG_GROUP}")
print("Deploy complete.")
