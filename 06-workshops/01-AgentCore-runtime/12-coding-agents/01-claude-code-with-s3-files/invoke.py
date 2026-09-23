"""
Invoke the Claude Code agent deployed on AgentCore Runtime.

Usage:
    python invoke.py
    python invoke.py "Write a Python function that sorts a list"
    python invoke.py --session <id> "Now add type hints to it"
"""

import json
import os
import sys
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

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


def invoke(runtime_arn: str, prompt: str, region: str, session_id: str = None) -> dict:
    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(read_timeout=900),
    )

    if not session_id:
        session_id = str(uuid.uuid4())

    payload_data = {"prompt": prompt}

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps(payload_data).encode("utf-8"),
        )
    except ClientError as exc:
        request_id = exc.response.get("ResponseMetadata", {}).get("RequestId", "N/A")
        print(f"  Session ID:      {session_id}")
        print(f"  Request ID:      {request_id}")
        print(f"  Error:           {exc}")
        return {"_runtimeSessionId": session_id}

    body = json.loads(response["response"].read().decode("utf-8"))
    runtime_session = response.get("runtimeSessionId", session_id)
    request_id = response.get("ResponseMetadata", {}).get("RequestId", "N/A")

    print(f"  Session ID:      {runtime_session}")
    print(f"  Request ID:      {request_id}")
    print(f"  Status:          {response.get('statusCode', 'N/A')}")

    body["_runtimeSessionId"] = runtime_session
    return body


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

    if args:
        prompts = [" ".join(args)]
    else:
        prompts = [
            "What is 2 + 2?",
            "Now multiply that result by 10.",
        ]

    print(f"Invoking agent: {runtime_arn}")
    if session_id:
        print(f"Resuming session: {session_id}")
    print()

    for prompt in prompts:
        print(f"--- Prompt: {prompt}")
        result = invoke(runtime_arn, prompt, region, session_id)
        print(f"--- Response:\n{result.get('response', result)}")
        session_id = result.get("_runtimeSessionId", session_id)
        print(f"--- Session ID: {session_id}")
        print()

    if session_id:
        print("To continue this conversation:")
        print(f'  python invoke.py --session {session_id} "your next prompt"')


if __name__ == "__main__":
    main()
