"""
Deploy the Claude Agent SDK HR Assistant to AgentCore Runtime.

Creates:
  - AgentCore Runtime (container-based)
  - Enables observability (CloudWatch spans for evaluation)

Outputs:
  - agent_config.json (agent_id, agent_arn, cw_log_group, region)

Usage:
    python deploy.py [--region REGION] [--name NAME]

Prerequisites:
    - AWS credentials configured (aws configure)
    - Bedrock model access enabled for: us.anthropic.claude-sonnet-4-5-20250929-v1:0
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import boto3

_SCRIPT_DIR = Path(__file__).parent
_CONFIG_PATH = _SCRIPT_DIR / "agent_config.json"

parser = argparse.ArgumentParser(description="Deploy Claude Agent SDK HR Assistant to AgentCore")
parser.add_argument("--region", default="us-east-1", help="AWS region")
parser.add_argument("--name", default="hr-assistant-claude-sdk", help="Agent runtime name")
args = parser.parse_args()

REGION = args.region
AGENT_NAME = args.name

print("=" * 60)
print("Deploying Claude Agent SDK HR Assistant to AgentCore")
print("=" * 60)
print(f"  Region: {REGION}")
print(f"  Name  : {AGENT_NAME}")

# --- Step 1: Build and push container image ---
print("\n[1/3] Building container image via AgentCore CLI ...")
print("  (Using `agentcore deploy` which handles ECR push + runtime creation)")

result = subprocess.run(
    ["agentcore", "deploy", "--name", AGENT_NAME, "--region", REGION],
    cwd=str(_SCRIPT_DIR),
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    print(f"  ERROR: agentcore deploy failed:\n{result.stderr}")
    sys.exit(1)

print("  Deploy successful.")

# --- Step 2: Get runtime details ---
print("\n[2/3] Retrieving runtime details ...")

cp_client = boto3.client("bedrock-agentcore-control", region_name=REGION)

runtimes = cp_client.list_agent_runtimes()
agent_runtime = None
for rt in runtimes.get("agentRuntimeSummaries", []):
    if AGENT_NAME in rt.get("agentRuntimeName", ""):
        agent_runtime = rt
        break

if not agent_runtime:
    print(f"  ERROR: Runtime '{AGENT_NAME}' not found after deploy.")
    sys.exit(1)

AGENT_ID = agent_runtime["agentRuntimeId"]
AGENT_ARN = agent_runtime["agentRuntimeArn"]
print(f"  Agent ID : {AGENT_ID}")
print(f"  Agent ARN: {AGENT_ARN}")

# --- Step 3: Enable observability ---
print("\n[3/3] Enabling observability (CloudWatch spans) ...")

try:
    cp_client.update_agent_runtime(
        agentRuntimeId=AGENT_ID,
        observabilityConfiguration={"enabled": True},
    )
    print("  Observability enabled.")
except Exception as e:
    print(f"  Warning: Could not enable observability: {e}")
    print("  (May already be enabled or require manual configuration)")

# Derive CloudWatch log group
CW_LOG_GROUP = f"/aws/bedrock-agentcore/runtime/{AGENT_ID}"

# --- Save config ---
config = {
    "agent_id": AGENT_ID,
    "agent_arn": AGENT_ARN,
    "agent_name": AGENT_NAME,
    "cw_log_group": CW_LOG_GROUP,
    "region": REGION,
    "framework": "claude-agent-sdk",
}

_CONFIG_PATH.write_text(json.dumps(config, indent=2))
print(f"\n  Config saved: {_CONFIG_PATH}")

print("\n" + "=" * 60)
print("Deployment complete!")
print("=" * 60)
print(f"  Next: python evaluate.py --region {REGION}")
