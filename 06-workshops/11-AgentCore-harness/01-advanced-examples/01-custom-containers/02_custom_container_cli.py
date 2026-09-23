#!/usr/bin/env python3
"""
Custom Container + Command Line Example for AgentCore Harness

This script demonstrates:
  1. Creating a Harness with a custom container image
  2. Invoking the agent to write and run code in that runtime
  3. Running commands directly on the agent's VM via ExecuteCommand
  4. Cleaning up resources

Usage:
    # Pick a language preset (node, go, python)
    python 03_custom_container_cli.py --language node
    python 03_custom_container_cli.py --language go
    python 03_custom_container_cli.py --language python

    # Or specify a container image directly
    python 03_custom_container_cli.py --container public.ecr.aws/docker/library/rust:slim

    # Bring your own IAM role
    python 03_custom_container_cli.py --role-arn arn:aws:iam::123456789012:role/MyRole

    # Other options
    python 03_custom_container_cli.py --model us.anthropic.claude-sonnet-4-6
    python 03_custom_container_cli.py --skip-cleanup
    python 03_custom_container_cli.py --raw-events
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid

# Add project root so helpers are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from helper.iam import create_harness_role
from helper.client import get_agentcore_client

REGION = os.getenv("AWS_DEFAULT_REGION")

# ---------------------------------------------------------------------------
# Language presets — map friendly names to container URIs + demo messages
# ---------------------------------------------------------------------------
LANGUAGE_PRESETS = {
    "node": {
        "container": "public.ecr.aws/docker/library/node:slim",
        "message": (
            "Write a Node.js script that creates a simple HTTP server on port 3000 "
            "that returns JSON with the current time, Node.js version, and platform info. "
            "Save it to /tmp/server.js. Then use curl to test it (start the server in the "
            "background, curl localhost:3000, and kill the server). Show me the output."
        ),
    },
    "go": {
        "container": "public.ecr.aws/docker/library/golang:1.24",
        "message": (
            "Write a Go HTTP server that listens on port 3000 and returns a JSON response "
            "with the current time, Go version, OS, architecture, and number of CPUs. "
            "Initialize a Go module at /tmp/goserver, save the code as main.go, build it "
            "into a binary called 'goserver', then test it: start the binary in the background, "
            "curl localhost:3000, and kill the server. Show me the curl output."
        ),
    },
    "python": {
        "container": "public.ecr.aws/docker/library/python:3.12-slim",
        "message": (
            "Write a Python HTTP server using the http.server module that listens on port 3000 "
            "and returns JSON with the current time, Python version, OS, and platform info. "
            "Save it to /tmp/server.py. Then test it: start the server in the background, "
            "curl localhost:3000, and kill the server. Show me the output."
        ),
    },
}

# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

parser = argparse.ArgumentParser(
    description="Harness Custom Container Demo — attach any container image and invoke the agent.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=f"Available language presets: {', '.join(LANGUAGE_PRESETS.keys())}",
)
parser.add_argument(
    "--language",
    "-l",
    choices=LANGUAGE_PRESETS.keys(),
    default="node",
    help="Language preset — sets container + demo message (default: node)",
)
parser.add_argument(
    "--container",
    default=None,
    metavar="URI",
    help="Container image URI (overrides --language preset)",
)
parser.add_argument(
    "--message",
    "-m",
    default=None,
    help="Prompt to send to the agent (overrides --language preset)",
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
    "--system-prompt",
    default=None,
    metavar="TEXT",
    help="System prompt (default: auto-generated based on container)",
)
parser.add_argument(
    "--commands",
    nargs="*",
    metavar="CMD",
    help="Extra commands to run on the VM after invocation (e.g. 'node --version' 'ls /tmp')",
)
parser.add_argument("--skip-cleanup", action="store_true", help="Keep resources after the demo")
parser.add_argument("--raw-events", action="store_true", help="Print raw streaming events")
args = parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def aws_cp(*cli_args: str) -> dict:
    """Run an aws bedrock-agentcore-control command and return parsed JSON."""
    cmd = ["aws", "bedrock-agentcore-control", "--region", REGION]
    cmd.extend(cli_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"CLI failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def wait_ready(harness_id: str, timeout: int = 120):
    """Poll until harness is READY."""
    deadline = time.monotonic() + timeout
    while True:
        status = aws_cp("get-harness", "--harness-id", harness_id)["harness"]["status"]
        print(f"  Status: {status}")
        if status == "READY":
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"Harness not ready after {timeout}s")
        time.sleep(5)


def stream_invoke(client, harness_arn, session_id, message, model_id):
    """Invoke a harness and stream the response."""
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": message}]}],
        model={"bedrockModelConfig": {"modelId": model_id}},
    )
    for event in response["stream"]:
        if args.raw_events:
            print(json.dumps(event, default=str))
        elif "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                print(f"\n  [Tool: {start['toolUse'].get('name', '?')}]", flush=True)
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
        elif "messageStop" in event:
            print()
        elif "internalServerException" in event:
            print(f"\n  Error: {event['internalServerException']}")


def run_command(client, harness_arn, session_id, command):
    """Execute a command on the agent's VM."""
    print(f"  $ {command}")
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": command},
    )
    for event in resp["stream"]:
        if args.raw_events:
            print(json.dumps(event, default=str))
        elif "chunk" in event:
            chunk = event["chunk"]
            if "contentDelta" in chunk:
                d = chunk["contentDelta"]
                if "stdout" in d:
                    print(f"  {d['stdout']}", end="", flush=True)
                if "stderr" in d:
                    print(f"  {d['stderr']}", end="", flush=True)
            elif "contentStop" in chunk:
                print(f"  [exit: {chunk['contentStop']['exitCode']}]")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Resolve language preset — explicit --container / --message override the preset
    preset = LANGUAGE_PRESETS[args.language]
    container_uri = args.container or preset["container"]
    message = args.message or preset["message"]
    model_id = args.model

    # Auto-generate system prompt from the container image name if not provided
    if args.system_prompt:
        system_prompt = args.system_prompt
    else:
        image_name = container_uri.rsplit("/", 1)[-1].split(":")[0]
        system_prompt = (
            f"You are a helpful coding assistant. You have access to a {image_name} runtime. "
            "When asked to write and run code, save it to a file and execute it using the shell."
        )

    harness_id = None
    try:
        # Step 0: IAM role
        print("=" * 60)
        print("Step 0: IAM execution role")
        print("=" * 60)
        if args.role_arn:
            role_arn = args.role_arn
            print(f"  Using provided role: {role_arn}")
        else:
            role_arn = create_harness_role()

        # Step 1: Create harness
        print("\n" + "=" * 60)
        print("Step 1: Create Harness")
        print("=" * 60)
        name = f"ContainerCLI_{uuid.uuid4().hex[:8]}"
        resp = aws_cp("create-harness", "--harness-name", name, "--execution-role-arn", role_arn)
        harness_id = resp["harness"]["harnessId"]
        harness_arn = resp["harness"]["arn"]
        print(f"  Harness ID:  {harness_id}")
        print(f"  Harness ARN: {harness_arn}")
        wait_ready(harness_id)

        # Step 2: Attach custom container
        print("\n" + "=" * 60)
        print(f"Step 2: Attach custom container ({container_uri})")
        print("=" * 60)
        aws_cp(
            "update-harness",
            "--harness-id",
            harness_id,
            "--environment-artifact",
            json.dumps({"optionalValue": {"containerConfiguration": {"containerUri": container_uri}}}),
            "--system-prompt",
            json.dumps([{"text": system_prompt}]),
        )
        wait_ready(harness_id)

        # Step 3: Invoke agent
        print("\n" + "=" * 60)
        print("Step 3: Invoke agent")
        print("=" * 60)

        client = get_agentcore_client()
        session_id = str(uuid.uuid4()).upper()
        print(f"  Session ID: {session_id}")
        print(f"  Model:      {model_id}")
        print(f"  Message:    {message[:80]}{'...' if len(message) > 80 else ''}\n")
        stream_invoke(client, harness_arn, session_id, message, model_id)

        # Step 4: ExecuteCommand
        print("\n" + "=" * 60)
        print("Step 4: Run commands on the agent's VM (ExecuteCommand)")
        print("=" * 60)
        default_commands = ["cat /etc/os-release | head -3", "ls /tmp/"]
        commands = args.commands if args.commands else default_commands
        for cmd in commands:
            run_command(client, harness_arn, session_id, cmd)

        print("\n" + "=" * 60)
        print("Done!")
        print("=" * 60)

    finally:
        if harness_id and not args.skip_cleanup:
            print("\nCleaning up...")
            try:
                aws_cp("delete-harness", "--harness-id", harness_id)
                print(f"  Deleted harness: {harness_id}")
            except Exception as e:
                print(f"  Warning: cleanup failed: {e}")


if __name__ == "__main__":
    main()
