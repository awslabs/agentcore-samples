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
    4. Inspect the files the agent created (ExecuteCommand) and check there are some
    5. Clean up

The point: a capable, AWS-aware coding agent in ~3 API calls. Swap the skill
paths or the prompt and you have a different agent — that's the harness model.

Step 4 is what makes this a demo of a *building* agent rather than a talking one,
so it verifies instead of narrating: an agent that describes files it never wrote
is exactly the failure this sample exists to rule out, and the closing "Done!"
now depends on the VM agreeing that the files are there.

Usage:
    # Build the default serverless URL-shortener agent
    python aws_builder_agent.py

    # Give it your own brief
    python aws_builder_agent.py \\
        -m "Design and scaffold a CDK app for an S3 + Lambda thumbnail pipeline."

    # Narrow the skills the agent loads
    python aws_builder_agent.py --skill-paths core-skills/aws-cdk core-skills/aws-serverless

    # Keep the harness after the demo (inspect the VM yourself)
    python aws_builder_agent.py --skip-cleanup

    # Pick the model, or reuse an execution role you already have
    python aws_builder_agent.py --model global.anthropic.claude-haiku-4-5-20251001-v1:0
    python aws_builder_agent.py --role-arn arn:aws:iam::111122223333:role/MyHarnessRole

    # Print the raw streaming events instead of the rendered reply
    python aws_builder_agent.py --raw-events

    # See all options
    python aws_builder_agent.py --help
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

# ── Constants ───────────────────────────────────────────────────────────────
DEFAULT_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_SKILL_PATHS = ["core-skills/aws-serverless", "core-skills/aws-cdk"]
PROJECT_DIR = "/tmp/url-shortener"

# Error members of the InvokeAgentRuntimeCommand event stream (per the boto3
# model). Depending on how the service frames the message, one of these either
# arrives as an ordinary event keyed by its member name or is raised out of the
# iterator as an EventStreamError, so both paths have to be handled. A loop that
# only reads chunks treats a command that never ran as one that produced no
# output — which is precisely how this sample used to fail.
COMMAND_ERROR_KEYS = (
    "accessDeniedException",
    "internalServerException",
    "resourceNotFoundException",
    "serviceQuotaExceededException",
    "throttlingException",
    "validationException",
    "runtimeClientError",
)

# The InvokeHarness stream models a smaller set — it has no accessDenied /
# resourceNotFound / serviceQuotaExceeded / throttling members of its own, so
# those can only reach the caller as a ClientError raised out of the iterator.
STREAM_ERROR_KEYS = (
    "internalServerException",
    "validationException",
    "runtimeClientError",
)

# stopReason values that mean the turn was cut short rather than finished. The
# agent hitting one of these is the normal way a scaffold step ends up with only
# half the files written, so it has to be reported — not printed as a blank line.
INCOMPLETE_STOP_REASONS = (
    "max_tokens",
    "max_output_tokens_exceeded",
    "max_iterations_exceeded",
    "timeout_exceeded",
    "model_context_window_exceeded",
    "content_filtered",
    "malformed_model_output",
    "malformed_tool_use",
    "interrupted",
    "partial_turn",
)

# Both prompts say what *not* to produce as well as what to produce. Left open,
# the agent treated the design turn as a licence to start writing files and then
# spent iteration after iteration generating summary documents about them —
# measured at 45 tool calls across the two turns (25 in what was supposed to be a
# design-only turn), most of the tail being SUMMARY.txt / INDEX.md /
# DELIVERABLES.md churn. Bounding each turn to its own job brought the same runs
# down to 11-15 tool calls.
DESIGN_PROMPT = (
    "Design a minimal serverless URL shortener on AWS: API Gateway + Lambda + "
    "DynamoDB. Consult your AWS skills first, then describe the architecture, the "
    "data model, and the two endpoints (create short URL, resolve short URL). "
    "Keep it to a short, concrete design, and answer in your reply — the files "
    "come in the next step."
)
SCAFFOLD_PROMPT = (
    f"Now scaffold that project under {PROJECT_DIR}. Create a CDK app (TypeScript) "
    "with the stack definition, a lambda/ directory with the two handler files, a "
    "README.md, and a package.json. Write real, runnable starter code — not "
    "placeholders. When every file is written, list them once and stop — no "
    "summary or index documents beyond the README."
)

# The `find` in the original inspect step does not exist on the harness image
# (Amazon Linux 2023 — and neither do `tree` or `xargs`), so it failed with
# "find: command not found" and the step listed nothing on a project the agent
# had really written. Three separate things hid that: `2>/dev/null` swallowed the
# message, nobody read the exit code, and `| head -40` meant the exit code was
# `head`'s anyway — a pipeline reports its *last* command's status, so the 127
# arrived as a 0 and would have looked like success even if it had been checked.
#
# Bash 5.2 is on the image, and `globstar` walks the tree without any external
# binary. No pipeline, so the status is the command's own. `test -d` runs first so
# a missing project directory is reported as missing rather than as an empty one,
# and the body is `if/then/fi` rather than `[ -f "$f" ] && echo "$f"` so a final
# glob match that happens to be a directory cannot leave a non-zero status behind
# and fail the whole step. Verified live against a harness VM for all of: missing
# dir, empty dir, dirs-only, nested/dotfile/spaced names, symlinks, and a plain
# file sitting where the directory should be.
LIST_FILES_CMD = (
    f"test -d {PROJECT_DIR} || {{ echo 'no such directory: {PROJECT_DIR}' >&2; exit 1; }}; "
    "shopt -s globstar dotglob nullglob; "
    f'for f in {PROJECT_DIR}/**; do if [ -f "$f" ]; then echo "$f"; fi; done'
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
    """Invoke the harness for one conversational turn and stream the response.

    Returns (text, stop_reason). The stop reason is the only thing that says
    whether the agent finished or was cut off part-way, and the caller needs it:
    the scaffold turn is what puts the files on the VM, so a turn that ran out of
    iterations or wall-clock leaves a half-written project behind.
    """
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": message}]}],
        model={"bedrockModelConfig": {"modelId": model_id}},
        timeoutSeconds=300,
    )

    full_text = ""
    stop_reason = None
    # A failure mid-stream can also be raised out of the iterator instead of
    # arriving as one of the events handled below: a modeled service error
    # (EventStreamError and other ClientErrors — throttling, access denied) or a
    # transport error (ReadTimeoutError, connection reset — a BotoCoreError, which
    # this agent can trigger just by going quiet during a long file-writing tool
    # call). Catching EventStreamError alone missed the transport half:
    # ReadTimeoutError is not a subclass of it, so a read timeout aborted the run
    # with a traceback that said nothing about the cause.
    try:
        for event in response["stream"]:
            if raw:
                print(json.dumps(event, default=str))
                # Keep reading the fields that matter in raw mode too, so the
                # caller's checks below behave the same either way. Skipping them
                # left full_text empty and stop_reason unset, which made every
                # --raw-events run look like a turn that produced nothing.
                delta = event.get("contentBlockDelta", {}).get("delta", {})
                if "text" in delta:
                    full_text += delta["text"]
                if "messageStop" in event:
                    stop_reason = event["messageStop"].get("stopReason")
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
                stop_reason = event["messageStop"].get("stopReason")
                print()
            else:
                err = next((event[k] for k in STREAM_ERROR_KEYS if k in event), None)
                if err is not None:
                    raise RuntimeError(f"Harness stream error: {err}")
    except (BotoCoreError, ClientError) as e:
        # The stream can fail on close after delivering the whole answer. Say so
        # either way, but only re-raise when nothing arrived — otherwise a
        # cosmetic close error throws away a complete, correct response.
        print(f"\n  Stream error: {e}")
        if not full_text:
            raise

    if stop_reason in INCOMPLETE_STOP_REASONS:
        print(f"\n  ⚠️  Turn ended early — stopReason: {stop_reason}")

    return full_text, stop_reason


def run_command(client, harness_arn, session_id, command):
    """Run a shell command on the agent's VM. Returns (stdout, exit_code).

    stdout is streamed as it arrives, so callers must not print the return value
    again. The stream reports failure two different ways and both have to be
    read: a command that ran and failed reports it in `contentStop.exitCode`,
    while a command that never ran arrives as one of the modeled error events.
    Reading neither is what let `find: command not found` pass for an empty
    project directory.
    """
    print(f"  $ {command}")
    output = ""
    exit_code = None
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": command},
    )
    for event in resp["stream"]:
        if "chunk" in event:
            chunk = event["chunk"]
            if "contentDelta" in chunk:
                delta = chunk["contentDelta"]
                if "stdout" in delta:
                    output += delta["stdout"]
                    print(delta["stdout"], end="", flush=True)
                if "stderr" in delta:
                    print(delta["stderr"], end="", flush=True)
            elif "contentStop" in chunk:
                exit_code = chunk["contentStop"].get("exitCode")
        else:
            err = next((event[k] for k in COMMAND_ERROR_KEYS if k in event), None)
            if err is not None:
                raise RuntimeError(f"Command stream error ({command}): {err}")
    if output and not output.endswith("\n"):
        print()
    return output, exit_code


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
        design_text, _ = stream_turn(client, harness_arn, session_id, design_prompt, args.model, raw=args.raw_events)
        # Step 3 says "now scaffold *that* project", so it only means something if
        # this turn actually produced a design. An empty reply here used to flow
        # straight into a scaffold prompt with no antecedent.
        if not design_text.strip():
            raise RuntimeError("The design turn returned no text — nothing for the scaffold step to build on")

        # ── Step 3: Scaffold (same session — VM state persists) ───────
        # Same session_id on purpose: the VM filesystem belongs to the session, so
        # a new one would start empty and Step 4 would find nothing no matter what
        # the agent wrote here.
        print("\n" + "=" * 60)
        print("Step 3: Scaffold the project on the agent's VM")
        print("=" * 60 + "\n")
        _, scaffold_stop = stream_turn(
            client, harness_arn, session_id, SCAFFOLD_PROMPT, args.model, raw=args.raw_events
        )

        # ── Step 4: Inspect what the agent built ──────────────────────
        print("\n" + "=" * 60)
        print("Step 4: Inspect the generated project")
        print("=" * 60)
        listing, exit_code = run_command(client, harness_arn, session_id, LIST_FILES_CMD)
        if exit_code is None:
            raise RuntimeError("Inspect command returned no exit status — nothing confirms it ran")
        if exit_code != 0:
            raise RuntimeError(f"Could not list {PROJECT_DIR} on the VM (exit {exit_code})")

        files = [line.strip() for line in listing.splitlines() if line.strip()]
        # The agent's own account of its work is not evidence. In a run against
        # the original script it finished by announcing "All files are in
        # /tmp/url-shortener/ — ready to deploy now!", the inspect step returned
        # nothing at all, and the script still printed its success line and
        # exited 0. Whether the files are there is a question for the VM.
        if not files:
            # The scaffold turn's stop reason is the first thing to look at here:
            # it distinguishes an agent that ran out of iterations or wall-clock
            # from one that simply narrated work it never did.
            detail = f" The scaffold turn ended with stopReason={scaffold_stop}." if scaffold_stop else ""
            raise RuntimeError(
                f"The agent reported success but left no files under {PROJECT_DIR}.{detail} "
                "Re-run with --skip-cleanup and inspect the harness to see what it did instead."
            )

        print(f"\n  {len(files)} file(s) written by the agent")
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
