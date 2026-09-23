"""
Execute a shell command on a running Kiro AgentCore Runtime session.

Usage:
    python exec_cmd.py --session <id> "ls -la /mnt/s3files"
    python exec_cmd.py --session <id> "kiro-cli whoami"
"""

import json
import os
import sys

import boto3
from botocore.config import Config


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config() -> dict:
    config_path = os.path.join(SCRIPT_DIR, "runtime_config.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy-kiro.py first.")
        sys.exit(1)


def exec_command(runtime_arn: str, session_id: str, command: str, region: str):
    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(read_timeout=900),
    )

    response = client.invoke_agent_runtime_command(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        body={"command": command},
    )

    print(f"Session: {response.get('runtimeSessionId', 'N/A')}")
    print(f"Request: {response.get('ResponseMetadata', {}).get('RequestId', 'N/A')}")
    print()

    for event in response["stream"]:
        chunk = event.get("chunk", {})
        if "contentDelta" in chunk:
            delta = chunk["contentDelta"]
            if delta.get("stdout"):
                print(delta["stdout"], end="", flush=True)
            if delta.get("stderr"):
                print(delta["stderr"], end="", file=sys.stderr, flush=True)


def main():
    config = load_config()
    runtime_arn = config["runtime_arn"]
    region = config["region"]

    args = sys.argv[1:]
    session_id = None

    if "--session" in args:
        idx = args.index("--session")
        session_id = args[idx + 1]
        args = args[:idx] + args[idx + 2 :]

    if not session_id:
        session_id = os.environ.get("SESSION_ID")

    if not session_id:
        print("Error: session ID required. Use --session <id> or set SESSION_ID env var.")
        sys.exit(1)

    if not args:
        print('Usage: python exec_cmd.py --session <id> "<command>"')
        sys.exit(1)

    command = " ".join(args)

    print(f"Runtime: {runtime_arn}")
    print(f"Command: {command}")
    print()

    exec_command(runtime_arn, session_id, command, region)


if __name__ == "__main__":
    main()
