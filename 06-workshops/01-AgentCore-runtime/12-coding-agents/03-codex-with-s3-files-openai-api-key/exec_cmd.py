"""
Execute a shell command on a running AgentCore Runtime session and stream output.

Usage:
    python exec_cmd.py --session <id> "ls -la /mnt/s3files"
"""

import json
import os
import sys

import boto3
from botocore.config import Config

# ── Load config ──────────────────────────────────────────────────────────────

def load_dotconfig():
    config_path = os.path.join(os.path.dirname(__file__), "envvars.config")
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    cfg[key] = value.strip('"').strip("'")
    return cfg

file_cfg = load_dotconfig()

def cfg(key, default=None):
    return file_cfg.get(key) or os.environ.get(key) or default

# ── Configuration ────────────────────────────────────────────────────────────

REGION = cfg("AGENTCORE_REGION", "us-west-2")


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "runtime_config.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


def exec_command(runtime_arn: str, session_id: str, command: str):
    client = boto3.client(
        "bedrock-agentcore",
        region_name=REGION,
        config=Config(read_timeout=900),
    )

    body = {"command": command}

    response = client.invoke_agent_runtime_command(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        body=body,
    )

    request_id = response.get("ResponseMetadata", {}).get("RequestId", "N/A")

    print(f"Session:    {response.get('runtimeSessionId', 'N/A')}")
    print(f"Request ID: {request_id}")
    print(f"Status:     {response.get('statusCode', 'N/A')}")
    print()

    for event in response["stream"]:
        chunk = event.get("chunk", {})
        if "contentDelta" in chunk:
            delta = chunk["contentDelta"]
            print(delta.get("stdout", ""), end="", flush=True)
            print(delta.get("stderr", ""), end="", flush=True)


def main():
    config = load_config()
    runtime_arn = config["runtime_arn"]

    args = sys.argv[1:]
    session_id = None

    if "--session" in args:
        idx = args.index("--session")
        session_id = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if not session_id:
        session_id = os.environ.get("SESSION_ID")

    if not session_id:
        print("Error: session ID required. Use --session <id> or set SESSION_ID env var.")
        sys.exit(1)

    if not args:
        print("Usage: python exec_cmd.py --session <id> '<command>'")
        sys.exit(1)

    command = " ".join(args)

    print(f"Runtime: {runtime_arn}")
    print(f"Command: {command}")
    print()

    exec_command(runtime_arn, session_id, command)


if __name__ == "__main__":
    main()
