"""
Invoke the HR Assistant agent deployed by deploy.py.

Reads runtime state from agent_state_{name}.json and sends sample HR prompts.

Usage:
    python invoke.py --name HRAssistV1 [--region us-east-1]
    python invoke.py --name HRAssistV1 --prompt "What is the PTO balance for EMP-001?"

Prerequisites:
    - Run deploy.py first to create agent_state_{name}.json
"""

import argparse
import json
import os
import uuid
from pathlib import Path

import boto3

# ── Parse arguments ───────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Invoke deployed HR Assistant")
parser.add_argument("--name", required=True, help="Runtime name used in deploy.py")
parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
parser.add_argument("--prompt", default=None, help="Custom prompt (optional)")
args = parser.parse_args()

STATE_FILE = Path(f"agent_state_{args.name}.json")
if not STATE_FILE.exists():
    raise FileNotFoundError(f"{STATE_FILE} not found. Run 'python deploy.py --name {args.name}' first.")

state = json.loads(STATE_FILE.read_text())
AGENT_ARN = state["runtime_arn"]
REGION = state.get("region", args.region)

dp = boto3.client("bedrock-agentcore", region_name=REGION)

print(f"Runtime : {args.name}")
print(f"ARN     : {AGENT_ARN}")
print()

# ── Sample prompts ────────────────────────────────────────────────────────

SAMPLE_PROMPTS = [
    ("EMP-001", "What is my current PTO balance?"),
    (
        "EMP-001",
        "Please submit a PTO request from 2026-06-01 to 2026-06-05 for a family vacation.",
    ),
    ("EMP-042", "Tell me about the 401k plan — how much does the company match?"),
    (
        "EMP-001",
        "What are my health insurance options and how much does the company cover?",
    ),
    ("EMP-002", "What is the parental leave policy for primary caregivers?"),
]

prompts_to_run = [("custom", args.prompt)] if args.prompt else SAMPLE_PROMPTS

for emp_id, prompt in prompts_to_run:
    session_id = str(uuid.uuid4())
    full_prompt = prompt if emp_id == "custom" else f"Employee ID: {emp_id}. {prompt}"
    print(f"[{emp_id}] {prompt}")

    resp = dp.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": full_prompt}).encode(),
    )
    response_text = resp["response"].read().decode("utf-8")
    print(f"Response: {response_text[:300]}")
    print()
