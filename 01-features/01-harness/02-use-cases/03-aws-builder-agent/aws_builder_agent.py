"""
AWS Builder Agent — building agents with the harness + AWS Skills

This use case answers a simple question: *how do you use the harness to build a
real agent?* The answer is that the harness IS the agent — you declare the model,
the tools, and the skills in one `create_harness` call, then invoke it. No
orchestration code, no framework.

Here we build an **AWS engineering assistant**: a harness agent loaded with the
[AWS Agent Toolkit](https://github.com/aws/agent-toolkit-for-aws) skills
(`awsSkills`). The agent gains curated AWS expertise — serverless, CDK,
CloudFormation, observability — and uses its built-in filesystem + shell tools to
actually scaffold a project, not just describe one.

What it does, end to end:

    1. Create a harness with AWS Skills + a builder system prompt
    2. Turn 1 — ask the agent to DESIGN a small serverless app (architecture)
    3. Turn 2 — same session: ask it to SCAFFOLD the project (write files to the VM)
    4. Inspect the files the agent created (ExecuteCommand)
    5. Clean up

The point: a capable, AWS-aware coding agent in ~3 API calls. Swap the skill
paths or the prompt and you have a different agent — that's the harness model.

Usage:
    # Build the default serverless URL-shortener agent
    python aws_builder_agent.py

    # Give it your own brief
    python aws_builder_agent.py \\
        -m "Design and scaffold a CDK app for an S3 + Lambda thumbnail pipeline."

    # Narrow the skills the agent loads
    python aws_builder_agent.py --skill-paths core-skills/aws-cdk core-skills/aws-serverless

    # Keep the harness after the demo
    python aws_builder_agent.py --skip-cleanup

    # See all options
    python aws_builder_agent.py --help
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
import botocore.exceptions

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import get_agentcore_client, get_agentcore_control_client
from utils.harness import poll_harness_status
from utils.iam import create_harness_role, delete_harness_role

REGION = os.getenv("AWS_DEFAULT_REGION")


# ── Constants ───────────────────────────────────────────────────────────────
DEFAULT_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_SKILL_PATHS = ["core-skills/aws-serverless", "core-skills/aws-cdk"]
PROJECT_DIR = "/tmp/url-shortener"

DESIGN_PROMPT = (
    "Design a minimal serverless URL shortener on AWS: API Gateway + Lambda + "
    "DynamoDB. Describe the architecture, the data model, and the two endpoints "
    "(create short URL, resolve short URL). Keep it to a short, concrete design."
)
SCAFFOLD_PROMPT = (
    f"Now scaffold that project under {PROJECT_DIR}. Create a CDK app (TypeScript) "
    "with the stack definition, a lambda/ directory with the two handler files, a "
    "README.md, and a package.json. Write real, runnable starter code — not "
    "placeholders. When done, list the files you created."
)


# ── CLI ─────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description="Build an AWS engineering agent with the harness + AWS Skills.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument(
    "--message",
    "-m",
    default=None,
    help="Override the design brief (the scaffold step follows automatically)",
)
parser.add_argument(
    "--skill-paths",
    nargs="+",
    default=DEFAULT_SKILL_PATHS,
    metavar="PATH",
    help=f"AWS skill paths to load (default: {' '.join(DEFAULT_SKILL_PATHS)})",
)
parser.add_argument(
    "--model",
    default=DEFAULT_MODEL,
    metavar="MODEL_ID",
    help=f"Bedrock model ID (default: {DEFAULT_MODEL})",
)
parser.add_argument(
    "--role-arn",
    default=None,
    metavar="ARN",
    help="Use an existing IAM execution role ARN instead of creating one",
)
parser.add_argument(
    "--skip-cleanup",
    action="store_true",
    help="Keep the harness after the demo",
)
parser.add_argument(
    "--raw-events",
    action="store_true",
    help="Print raw JSON streaming events from invoke",
)


# ── Helpers ─────────────────────────────────────────────────────────────────
def stream_turn(client, harness_arn, session_id, message, model_id, raw=False):
    """Invoke the harness for one conversational turn and stream the response."""
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": message}]}],
        model={"bedrockModelConfig": {"modelId": model_id}},
        timeoutSeconds=300,
    )

    full_text = ""
    try:
        for event in response["stream"]:
            if raw:
                print(json.dumps(event, default=str))
                continue

            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                if "toolUse" in start:
                    print(f"\n  [Tool: {start['toolUse'].get('name', '?')}]", flush=True)
            elif "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    print(delta["text"], end="", flush=True)
                    full_text += delta["text"]
            elif "messageStop" in event:
                print()
            elif "internalServerException" in event:
                print(f"\n  Error: {event['internalServerException']}")
    except botocore.exceptions.EventStreamError:
        if not full_text:
            raise

    return full_text


def run_command(client, harness_arn, session_id, command):
    """Run a shell command on the agent's VM and print stdout/stderr."""
    print(f"  $ {command}")
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": command},
    )
    for event in resp["stream"]:
        if "chunk" in event and "contentDelta" in event["chunk"]:
            delta = event["chunk"]["contentDelta"]
            if "stdout" in delta:
                print(delta["stdout"], end="")
            if "stderr" in delta:
                print(delta["stderr"], end="")
    print()


# ── Main ────────────────────────────────────────────────────────────────────
def main(args=None):
    if args is None:
        args = parser.parse_args()

    control = get_agentcore_control_client()
    client = get_agentcore_client()

    design_prompt = args.message or DESIGN_PROMPT
    skills = [{"awsSkills": {"paths": args.skill_paths}}]
    harness_id = None
    created_role = False

    try:
        # ── Step 0: IAM role ──────────────────────────────────────────
        print("=" * 60)
        print("Step 0: IAM execution role")
        print("=" * 60)
        if args.role_arn:
            role_arn = args.role_arn
            print(f"  Using provided role: {role_arn}")
        else:
            role_arn = create_harness_role()
            created_role = True
            print("  Waiting for IAM propagation...")
            time.sleep(10)

        # ── Step 1: Create the agent (harness + AWS Skills) ───────────
        print("\n" + "=" * 60)
        print("Step 1: Create the AWS builder agent")
        print("=" * 60)
        print(f"  AWS skills: {args.skill_paths}")
        harness_name = f"AwsBuilder_{uuid.uuid4().hex[:8]}"
        resp = control.create_harness(
            harnessName=harness_name,
            executionRoleArn=role_arn,
            skills=skills,
            systemPrompt=[
                {
                    "text": (
                        "You are a senior AWS solutions engineer. Use your AWS skills to "
                        "design and build well-architected, runnable projects. Prefer "
                        "infrastructure-as-code and serverless best practices. When asked to "
                        "scaffold, write real files to the filesystem using your tools."
                    )
                }
            ],
        )
        harness_id = resp["harness"]["harnessId"]
        harness_arn = resp["harness"]["arn"]
        print(f"  Harness ID:  {harness_id}")
        print(f"  Harness ARN: {harness_arn}")
        poll_harness_status(control, harness_id)

        session_id = str(uuid.uuid4()).upper()
        print(f"  Session ID: {session_id}")

        # ── Step 2: Design ────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("Step 2: Design the solution")
        print("=" * 60)
        print(f"  Brief: {design_prompt[:80]}{'...' if len(design_prompt) > 80 else ''}\n")
        stream_turn(client, harness_arn, session_id, design_prompt, args.model, raw=args.raw_events)

        # ── Step 3: Scaffold (same session — VM state persists) ───────
        print("\n" + "=" * 60)
        print("Step 3: Scaffold the project on the agent's VM")
        print("=" * 60 + "\n")
        stream_turn(client, harness_arn, session_id, SCAFFOLD_PROMPT, args.model, raw=args.raw_events)

        # ── Step 4: Inspect what the agent built ──────────────────────
        print("\n" + "=" * 60)
        print("Step 4: Inspect the generated project")
        print("=" * 60)
        run_command(client, harness_arn, session_id, f"find {PROJECT_DIR} -type f 2>/dev/null | head -40")

        print("=" * 60)
        print("Done! The harness + AWS Skills produced a working AWS agent.")
        print("=" * 60)

    finally:
        if not args.skip_cleanup:
            print("\nCleaning up...")
            if harness_id:
                try:
                    control.delete_harness(harnessId=harness_id)
                    print(f"  Deleted harness: {harness_id}")
                except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                    print(f"  Warning: failed to delete harness: {e}")
            # Delete the execution role too, but only the one we created — the
            # role name is shared by every sample in this folder, so deleting a
            # role the caller passed in with --role-arn would destroy something
            # we don't own. Without this the role outlived the script and was
            # left behind on any failure before Step 1 completed.
            if created_role:
                delete_harness_role()


if __name__ == "__main__":
    main()
