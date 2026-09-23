"""
AWS Skills for Harness

This script shows how to give a Harness agent native **AWS Skills** — curated
capability bundles from the AWS Agent Toolkit (https://github.com/aws/agent-toolkit-for-aws)
that are baked into the Harness runtime image. Unlike custom skills that you
install onto the VM yourself, AWS Skills are enabled declaratively through the
`skills` parameter and are ready the moment the agent starts.

A skill bundles instructions, reference docs, and code templates for a specific
AWS domain (serverless, CloudFormation, observability, cost management, ...).
They are especially valuable with smaller/cheaper models that don't carry deep
AWS knowledge — the skill supplies the patterns the model needs to succeed.

Four ways to select AWS Skills (pick with --mode):

    all       Enable every AWS Skill in the toolkit
                skills=[{"awsSkills": {}}]

    glob      Enable a whole category with a glob path (default)
                skills=[{"awsSkills": {"paths": ["core-skills/*"]}}]

    specific  Enable one named skill
                skills=[{"awsSkills": {"paths": [
                    "specialized-skills/operations-skills/troubleshooting-application-failures"]}}]

    mixed     Combine several AWS Skill selections (and you can add other skill
              sources — path/S3 — in the same array)
                skills=[
                    {"awsSkills": {"paths": ["core-skills/aws-cdk"]}},
                    {"awsSkills": {"paths": ["core-skills/aws-serverless"]}},
                ]

Skills can be set on the Harness resource (CreateHarness/UpdateHarness, so they
apply to every invocation) or passed per call on InvokeHarness. This sample sets
them on the resource at create time, then invokes.

Usage:
    # Default — enable all of core-skills/* and ask the agent what it can do
    python aws_skills.py

    # Enable every AWS Skill
    python aws_skills.py --mode all

    # Enable a single named skill
    python aws_skills.py --mode specific \\
        --skill-path specialized-skills/operations-skills/troubleshooting-application-failures

    # Combine serverless + CDK skills and ask the agent to design a workflow
    python aws_skills.py --mode mixed \\
        -m "Design a Step Functions state machine for order processing and outline the CDK stack."

    # Keep the harness after the demo
    python aws_skills.py --skip-cleanup

    # See all options
    python aws_skills.py --help
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import get_agentcore_client, get_agentcore_control_client
from utils.harness import poll_harness_status
from utils.iam import create_harness_role, delete_harness_role

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_GLOB = "core-skills/*"
DEFAULT_SPECIFIC = "specialized-skills/operations-skills/troubleshooting-application-failures"
DEFAULT_PROMPT = "What AWS skills do you have available? Give a short bulleted summary by category."


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(
    description="Enable native AWS Skills on a Harness and invoke the agent.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument(
    "--mode",
    choices=["all", "glob", "specific", "mixed"],
    default="glob",
    help="How to select AWS Skills (default: glob — enables core-skills/*)",
)
parser.add_argument(
    "--skill-path",
    default=DEFAULT_SPECIFIC,
    metavar="PATH",
    help=f"Skill path used by --mode specific (default: {DEFAULT_SPECIFIC})",
)
parser.add_argument(
    "--glob-path",
    default=DEFAULT_GLOB,
    metavar="PATH",
    help=f"Glob path used by --mode glob (default: {DEFAULT_GLOB})",
)
parser.add_argument(
    "--model",
    default=DEFAULT_MODEL,
    metavar="MODEL_ID",
    help=f"Bedrock model ID (default: {DEFAULT_MODEL})",
)
parser.add_argument(
    "--message",
    "-m",
    default=DEFAULT_PROMPT,
    help="Prompt to send to the agent",
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_skills(args):
    """Translate the chosen --mode into a `skills` parameter value.

    Each entry is a union; the `awsSkills` member selects skills from the AWS
    Agent Toolkit. An empty `{}` means "all skills"; `paths` narrows it with
    glob patterns or exact skill paths.
    """
    if args.mode == "all":
        return [{"awsSkills": {}}]
    if args.mode == "glob":
        return [{"awsSkills": {"paths": [args.glob_path]}}]
    if args.mode == "specific":
        return [{"awsSkills": {"paths": [args.skill_path]}}]
    # mixed — combine two category selections; add {"path": ...} or S3 sources here too
    return [
        {"awsSkills": {"paths": ["core-skills/aws-serverless"]}},
        {"awsSkills": {"paths": ["core-skills/aws-cdk"]}},
    ]


def stream_response(client, harness_arn, session_id, message, model_id, raw=False):
    """Invoke a Harness and stream the response to stdout."""
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": message}]}],
        model={"bedrockModelConfig": {"modelId": model_id}},
    )

    full_text = ""
    # A failure mid-stream is raised out of the iterator by botocore, not
    # delivered as an {"internalServerException": ...} event. It can be a modeled
    # service error (EventStreamError/ClientError — throttling, access denied) or
    # a transport error (ReadTimeoutError, connection reset — a BotoCoreError,
    # which the agent can trigger just by going quiet during a long tool call).
    # Catch both base classes: EventStreamError alone let a read timeout or a
    # throttle abort the script before the `finally` in main() had reported the
    # partial answer, and the traceback said nothing about the real cause.
    try:
        for event in response["stream"]:
            if raw:
                print(json.dumps(event, default=str))
                # Keep accumulating text in raw mode too. Skipping it left
                # full_text empty, so the error handling below could not tell a
                # stream that had produced content from one that had not.
                delta = event.get("contentBlockDelta", {}).get("delta", {})
                if "text" in delta:
                    full_text += delta["text"]
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
    except (BotoCoreError, ClientError) as e:
        # The stream may fail on close after delivering the whole answer. Report
        # it either way, but only re-raise when nothing arrived — otherwise a
        # cosmetic close error would throw away a complete, correct response.
        print(f"\n  Stream error: {e}")
        if not full_text:
            raise

    return full_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(args=None):
    if args is None:
        args = parser.parse_args()

    control = get_agentcore_control_client()
    client = get_agentcore_client()

    skills = build_skills(args)
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

        # ── Step 1: Create Harness with AWS Skills ────────────────────
        print("\n" + "=" * 60)
        print(f"Step 1: Create Harness with AWS Skills (mode: {args.mode})")
        print("=" * 60)
        print(f"  skills = {json.dumps(skills)}")
        harness_name = f"AwsSkills_{uuid.uuid4().hex[:8]}"
        resp = control.create_harness(
            harnessName=harness_name,
            executionRoleArn=role_arn,
            skills=skills,
            # No `tools`/`allowedTools` is set, so the harness keeps its built-in
            # toolset (fs/shell) — that is what lets the agent read the skill
            # files it was given and act on what they teach.
            systemPrompt=[{"text": "You are a helpful AWS engineering assistant."}],
        )
        harness_id = resp["harness"]["harnessId"]
        harness_arn = resp["harness"]["arn"]
        print(f"  Harness ID:  {harness_id}")
        print(f"  Harness ARN: {harness_arn}")
        # Harnesses that enable native AWS Skills routinely take ~3 minutes to
        # reach READY; the shared poller's default timeout already allows for it.
        poll_harness_status(control, harness_id)

        # ── Step 2: Invoke and observe the loaded skills ──────────────
        print("\n" + "=" * 60)
        print("Step 2: Invoke agent")
        print("=" * 60)
        session_id = str(uuid.uuid4()).upper()
        print(f"  Session ID: {session_id}")
        print(f"  Model:      {args.model}")
        print(f"  Message:    {args.message[:80]}{'...' if len(args.message) > 80 else ''}\n")

        stream_response(client, harness_arn, session_id, args.message, args.model, raw=args.raw_events)

        print("\n" + "=" * 60)
        print("Done!")
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
