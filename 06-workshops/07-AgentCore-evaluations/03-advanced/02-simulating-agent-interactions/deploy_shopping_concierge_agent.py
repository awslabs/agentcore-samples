"""Deploy the Shopping Concierge agent to AgentCore Runtime using the agentcore CLI.

Run from the notebook via: %run -i deploy_shopping_concierge_agent.py

Expects REGION to be set in the caller's namespace (Step 2 config cell).
Sets in the caller's namespace: AGENT_ID, AGENT_ARN, RUNTIME_ARN,
    SERVICE_NAME, LOG_GROUP, SPANS_LOG_GROUP

Uses the agentcore CLI (from bedrock-agentcore) which handles
CodeBuild image builds, ECR push, OTel instrumentation, and runtime creation.
"""

import subprocess
import time
import uuid

import boto3

_REGION = REGION  # noqa: F821
_AGENT_NAME = f"shopping_concierge_{uuid.uuid4().hex[:8]}"

# ---- 1. Configure ----
print(f"Configuring agent '{_AGENT_NAME}' ...")
subprocess.run(
    [
        "agentcore",
        "configure",
        "--entrypoint",
        "shopping_concierge_agent.py",
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
print("\nDeploying Shopping Concierge Agent ...")
print("  This takes ~5 minutes on first run (image build + push + runtime creation).")
subprocess.run(
    ["agentcore", "deploy", "--auto-update-on-conflict"],
    check=True,
)
print("Deploy complete.")

# ---- 3. Get agent ID ----
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

# ---- Set variables for the notebook ----
RUNTIME_ARN = AGENT_ARN
SERVICE_NAME = f"{_AGENT_NAME}.DEFAULT"
LOG_GROUP = f"/aws/bedrock-agentcore/runtimes/{AGENT_ID}-DEFAULT"
SPANS_LOG_GROUP = "aws/spans"

print(f"\nAGENT_ID     : {AGENT_ID}")
print(f"AGENT_ARN    : {AGENT_ARN}")
print(f"RUNTIME_ARN  : {RUNTIME_ARN}")
print(f"SERVICE_NAME : {SERVICE_NAME}")
print(f"LOG_GROUP    : {LOG_GROUP}")
print("Deploy complete.")
