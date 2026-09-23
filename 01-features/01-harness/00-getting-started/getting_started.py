"""
Getting Started with AgentCore Harness.

Demonstrates the core Harness workflow:
  1. Creating an IAM execution role with necessary permissions
  2. Creating a Harness agent
  3. Invoking the agent with a prompt and streaming the response
  4. Switching models in the same session
  5. Running shell commands on the agent's isolated microVM (ExecuteCommand)
  6. Cleanup

Usage:
    python getting_started.py

Prerequisites:
    - AWS CLI configured with credentials
    - pip install -r ../requirements.txt
    - AWS_DEFAULT_REGION environment variable set (or boto3 default region configured)
"""

import sys
import time
import uuid
from pathlib import Path

import boto3

# Add root utils to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.client import get_agentcore_client, get_agentcore_control_client
from utils.harness import poll_harness_status
from utils.iam import create_harness_role, delete_harness_role

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_HAIKU = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
MODEL_SONNET = "global.anthropic.claude-sonnet-4-6"

# ── Setup ─────────────────────────────────────────────────────────────────────
control = get_agentcore_control_client()
client = get_agentcore_client()

account_id = boto3.client("sts").get_caller_identity()["Account"]
print(f"Account: {account_id}")

# ── Create IAM Role ────────────────────────────────────────────────────────────
print("\n=== Step 1: IAM Execution Role ===")
role_arn = create_harness_role()
print(f"Execution Role ARN: {role_arn}")
print("Waiting for IAM role to propagate...")
time.sleep(10)
print("Ready!")

# ── Create Harness ─────────────────────────────────────────────────────────────
# Everything from here on runs under try/finally so the cleanup in Step 6 also
# happens when a step in between fails. The role is created above but deleted in
# that finally block, so the harness creation belongs inside the block too —
# otherwise a CreateHarness failure (an unavailable model, a role that has not
# finished propagating) would exit leaving the role behind.
print("\n=== Step 2: Create Harness ===")
HARNESS_NAME = f"GettingStarted_{uuid.uuid4().hex[:8]}"
harness_id = None

try:
    resp = control.create_harness(
        harnessName=HARNESS_NAME,
        executionRoleArn=role_arn,
    )
    harness = resp["harness"]
    harness_id = harness["harnessId"]
    harness_arn = harness["arn"]
    print(f"Harness ID:  {harness_id}")
    print(f"Harness ARN: {harness_arn}")
    print(f"Status:      {harness['status']}")

    poll_harness_status(control, harness_id)
    print("✅ Harness is ready")

    # ── Invoke Agent — Haiku ───────────────────────────────────────────────────────
    print("\n=== Step 3: Invoke Agent (Claude Haiku) ===")
    session_id = str(uuid.uuid4()).upper()
    print(f"Session ID: {session_id}\n")

    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": "What are three fun things to do in Seattle on a rainy day? "
                        "Save your answer to a Markdown file."
                    }
                ],
            }
        ],
        model={"bedrockModelConfig": {"modelId": MODEL_HAIKU}},
    )

    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                print(f"\n[Tool: {start['toolUse'].get('name', '?')}]", flush=True)
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
        elif "messageStop" in event:
            print()
        elif "internalServerException" in event:
            print(f"\nError: {event['internalServerException']}")

    # ── Reuse Same Session with Different Model ────────────────────────────────────
    print("\n=== Step 4: Reuse Session with Claude Sonnet ===")
    print(f"Session ID (same): {session_id}\n")

    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": "What are three fun things to do in Seattle on a rainy day? "
                        "Save your answer to a Markdown file with Sonnet prefix."
                    }
                ],
            }
        ],
        model={"bedrockModelConfig": {"modelId": MODEL_SONNET}},
    )

    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                print(f"\n[Tool: {start['toolUse'].get('name', '?')}]", flush=True)
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
        elif "messageStop" in event:
            print()
        elif "internalServerException" in event:
            print(f"\nError: {event['internalServerException']}")

    # ── ExecuteCommand — Run Commands on Agent's VM ────────────────────────────────
    print("\n=== Step 5: ExecuteCommand (agent's remote microVM) ===")

    def run(cmd: str):
        """Run a shell command on the agent's remote microVM."""
        print(f"$ {cmd}")
        resp = client.invoke_agent_runtime_command(
            agentRuntimeArn=harness_arn,
            runtimeSessionId=session_id,
            body={"command": cmd},
        )
        for event in resp["stream"]:
            if "chunk" in event:
                chunk = event["chunk"]
                if "contentDelta" in chunk:
                    d = chunk["contentDelta"]
                    if "stdout" in d:
                        print(d["stdout"], end="", flush=True)
                    if "stderr" in d:
                        print(d["stderr"], end="", flush=True)
                elif "contentStop" in chunk:
                    print(f"\n[exit: {chunk['contentStop']['exitCode']}]")
        print()

    run("ls -la")
    run("pwd")
    run('for f in *.md; do echo "=== $f ==="; cat "$f"; echo; done')

finally:
    # ── Cleanup ────────────────────────────────────────────────────────────────────
    # Delete each resource independently: harness_id is None if CreateHarness
    # itself failed, and a delete_harness error must not skip the role deletion —
    # a leftover role is what makes the next run of any sample in this folder
    # behave unpredictably, since they all share one role name.
    print("\n=== Step 6: Cleanup ===")
    if harness_id:
        try:
            control.delete_harness(harnessId=harness_id)
            print(f"Deleted harness: {harness_id}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"Warning: could not delete harness {harness_id}: {e}")

    delete_harness_role()
    print("Done.")
