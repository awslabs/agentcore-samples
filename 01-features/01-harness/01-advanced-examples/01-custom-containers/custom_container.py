"""
Custom Container with AgentCore Harness.

Demonstrates how to attach custom container images to a Harness so the agent
runs in your own environment (Node.js, Go, Python, Rust, etc.):
  Part 1: Create and update a Harness with a Node.js container
  Part 2: Invoke the agent to write and run Node.js code
  Part 3: Use ExecuteCommand to verify the VM environment directly
  Part 4: Install npm packages and use them (session persistence)
  Part 5: Go container — cross-compile binaries

Why custom containers?
  By default, Harness runs on Amazon Linux 2023 with Python. Custom containers
  unlock any runtime, system library, or pre-installed dependency. Containers
  must support linux/arm64 (Harness VMs run on ARM).

Usage:
    python custom_container.py [--language node|go|python] [--skip-cleanup]

    # Specify any container image directly
    python custom_container.py --container public.ecr.aws/docker/library/rust:slim

Prerequisites:
    - AWS CLI configured with credentials
    - pip install -r ../../requirements.txt
    - AWS_DEFAULT_REGION environment variable set
"""

import argparse
import sys
import time
import uuid
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import get_agentcore_client, get_agentcore_control_client
from utils.harness import poll_harness_status
from utils.iam import create_harness_role, delete_harness_role

# ── Language Presets ───────────────────────────────────────────────────────────
LANGUAGE_PRESETS = {
    "node": {
        "container": "public.ecr.aws/docker/library/node:slim",
        "system_prompt": "You are a helpful coding assistant with access to a Node.js runtime. When asked to write and run code, save it to a file and execute it using the shell.",
        "message": (
            "Write a Node.js script that creates a simple HTTP server on port 3000 "
            "that returns JSON with the current time, Node.js version, and platform info. "
            "Save it to /tmp/server.js. Then test it — start the server in the background, "
            "make an HTTP request using Node.js http module, and kill the server. Show the output."
        ),
    },
    "go": {
        "container": "public.ecr.aws/docker/library/golang:1.24",
        "system_prompt": "You are a helpful coding assistant with access to a Go toolchain. When asked to write and run code, save it to a file, build it, and execute it using the shell.",
        "message": (
            "Write a Go HTTP server that listens on port 3000 and returns a JSON response "
            "with the current time, Go version, OS, architecture, and number of CPUs. "
            "Initialize a Go module at /tmp/goserver, save the code as main.go, build it "
            "into a binary called 'goserver', then test it: start the binary in the background, "
            "curl localhost:3000, and kill the server. Show the curl output."
        ),
    },
    "python": {
        "container": "public.ecr.aws/docker/library/python:3.12-slim",
        "system_prompt": "You are a helpful coding assistant with access to a Python 3.12 runtime. When asked to write and run code, save it to a file and execute it using the shell.",
        "message": (
            "Write a Python HTTP server using http.server that listens on port 3000 "
            "and returns JSON with the current time, Python version, OS, and platform info. "
            "Save it to /tmp/server.py. Then test it: start the server in the background, "
            "make an HTTP request using Python's urllib (the python:3.12-slim image has no "
            "curl or wget), and kill the server. Show the output."
        ),
    },
}

# ── CLI ────────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description="Harness Custom Container Demo",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument(
    "--language",
    "-l",
    choices=LANGUAGE_PRESETS.keys(),
    default="node",
    help="Language preset (default: node)",
)
parser.add_argument("--container", default=None, metavar="URI", help="Override container image URI")
parser.add_argument("--message", "-m", default=None, help="Override prompt to agent")
parser.add_argument("--model", default="global.anthropic.claude-haiku-4-5-20251001-v1:0")
parser.add_argument("--role-arn", default=None, metavar="ARN", help="Use existing IAM role ARN")
parser.add_argument("--skip-cleanup", action="store_true", help="Keep resources after the demo")
args = parser.parse_args()

# ── Resolve preset ─────────────────────────────────────────────────────────────
preset = LANGUAGE_PRESETS[args.language]
container_uri = args.container or preset["container"]
message = args.message or preset["message"]
system_prompt = preset["system_prompt"]

# ── Setup ─────────────────────────────────────────────────────────────────────
control = get_agentcore_control_client()
client = get_agentcore_client()

account_id = boto3.client("sts").get_caller_identity()["Account"]
print(f"Account: {account_id}")
print(f"Language: {args.language}  Container: {container_uri}")

# The first invoke against a freshly attached container can race the microVM's
# health check. Three attempts 15s apart covers the lag seen in practice without
# masking a genuinely broken image, which fails every attempt the same way.
INVOKE_MAX_ATTEMPTS = 3
INVOKE_RETRY_DELAY = 15

# ── Helpers ───────────────────────────────────────────────────────────────────


def stream_invoke(harness_arn, session_id, message, model_id=args.model):
    # A harness reporting READY does not guarantee the custom container's microVM
    # is already answering health checks: the first invoke of a session boots a
    # fresh VM from the image, and that has been observed losing the race, with
    # InvokeHarness raising "Runtime health check failed or timed out" while the
    # container itself logged a clean startup. Retrying the call absorbs it.
    # Only the call is retried, never a stream that already yielded output, so no
    # text can be printed twice.
    for attempt in range(1, INVOKE_MAX_ATTEMPTS + 1):
        try:
            response = client.invoke_harness(
                harnessArn=harness_arn,
                runtimeSessionId=session_id,
                messages=[{"role": "user", "content": [{"text": message}]}],
                model={"bedrockModelConfig": {"modelId": model_id}},
            )
            break
        except client.exceptions.RuntimeClientError as e:
            if attempt == INVOKE_MAX_ATTEMPTS:
                raise
            print(
                f"  Runtime not ready yet ({e.response['Error']['Code']}); "
                f"retrying in {INVOKE_RETRY_DELAY}s "
                f"[attempt {attempt}/{INVOKE_MAX_ATTEMPTS}]"
            )
            time.sleep(INVOKE_RETRY_DELAY)
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


def run_command(harness_arn, session_id, command):
    """Run a command on the agent's remote microVM."""
    print(f"$ {command}")
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": command},
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


harness_id = None
created_role = False
try:
    # ── Step 0: IAM role ──────────────────────────────────────────────────────
    print("\n=== Step 0: IAM Role ===")
    if args.role_arn:
        role_arn = args.role_arn
        print(f"Using provided role: {role_arn}")
    else:
        role_arn = create_harness_role()
        created_role = True
        print("Waiting for IAM propagation...")
        time.sleep(10)

    # ── Step 1: Create Harness ────────────────────────────────────────────────
    # Name after the selected language, not a hardcoded "NodeContainer" — a Go or
    # Python run mislabelled as Node makes leaked resources (and their auto-named
    # managed memory) impossible to tell apart in the console.
    print("\n=== Step 1: Create Harness ===")
    HARNESS_NAME = f"{args.language.capitalize()}Container_{uuid.uuid4().hex[:8]}"
    resp = control.create_harness(harnessName=HARNESS_NAME, executionRoleArn=role_arn)
    harness = resp["harness"]
    harness_id = harness["harnessId"]
    harness_arn = harness["arn"]
    print(f"Harness ID:  {harness_id}")
    print(f"Harness ARN: {harness_arn}")

    # A harness routinely takes ~2-3 minutes to reach READY, so this has to wait
    # for the real thing rather than a fixed number of polls: falling through
    # while the harness is still CREATING made the update_harness call below fail
    # with "Cannot update agent ... while it is CREATING".
    poll_harness_status(control, harness_id)
    print("✅ Harness ready")

    # ── Step 2: Attach Custom Container ──────────────────────────────────────
    print(f"\n=== Step 2: Attach Custom Container ({container_uri}) ===")
    control.update_harness(
        harnessId=harness_id,
        environmentArtifact={"optionalValue": {"containerConfiguration": {"containerUri": container_uri}}},
        systemPrompt=[{"text": system_prompt}],
    )
    print("Waiting for container update...")
    poll_harness_status(control, harness_id)
    print("✅ Harness updated with custom container")

    # ── Step 3: Invoke Agent ─────────────────────────────────────────────────
    print("\n=== Step 3: Invoke Agent ===")
    session_id = str(uuid.uuid4()).upper()
    print(f"Session ID: {session_id}")
    print(f"Model:      {args.model}")
    print(f"Message:    {message[:80]}{'...' if len(message) > 80 else ''}\n")
    stream_invoke(harness_arn, session_id, message)

    # ── Step 4: ExecuteCommand — Verify VM Environment ────────────────────────
    print("\n=== Step 4: ExecuteCommand — Verify VM Environment ===")
    run_command(harness_arn, session_id, "cat /etc/os-release | head -3")
    run_command(harness_arn, session_id, "pwd && whoami")
    run_command(harness_arn, session_id, "ls -la /tmp/")

    if args.language == "node":
        run_command(harness_arn, session_id, "node --version && npm --version")
        run_command(
            harness_arn,
            session_id,
            "cat /tmp/server.js 2>/dev/null || echo 'No server.js found'",
        )

        # ── Step 5: Install npm package and use it ───────────────────────────
        print("\n=== Step 5: Install npm package (chalk) ===")
        stream_invoke(
            harness_arn,
            session_id,
            "Install the 'chalk' npm package (latest), then write a Node.js script at /tmp/colors.js "
            "that uses chalk to print a colorful welcome banner. Run it.",
        )

    elif args.language == "go":
        run_command(harness_arn, session_id, "go version")
        run_command(
            harness_arn,
            session_id,
            "ls -la /tmp/goserver/ 2>/dev/null || echo 'No goserver dir'",
        )

        # Cross-compile for linux/amd64
        print("\n=== Step 5: Go Cross-Compilation ===")
        stream_invoke(
            harness_arn,
            session_id,
            "Cross-compile the /tmp/goserver/main.go binary for linux/amd64. "
            "Use GOOS=linux GOARCH=amd64 go build -o /tmp/goserver_linux_amd64. "
            "Show the file size with ls -lh and the architecture with 'readelf -h' "
            "(the golang:1.24 image has no 'file' binary).",
        )

    elif args.language == "python":
        run_command(harness_arn, session_id, "python3 --version")
        run_command(
            harness_arn,
            session_id,
            "cat /tmp/server.py 2>/dev/null || echo 'No server.py found'",
        )

    print("\n=== Done! ===")

finally:
    if not args.skip_cleanup:
        print("\n=== Cleanup ===")
        if harness_id:
            try:
                control.delete_harness(harnessId=harness_id)
                print(f"Deleted harness: {harness_id}")
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                print(f"Warning: cleanup failed: {e}")
        # Only delete the role if we created it: --role-arn lets the caller pass
        # in their own role, and deleting that would destroy something we don't
        # own. Guarding on `harness_id` alone also meant a failure before the
        # harness existed skipped cleanup entirely and leaked the role.
        if created_role:
            delete_harness_role()
