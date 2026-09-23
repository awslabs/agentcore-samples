"""
Send a prompt to the Kiro agent on AgentCore Runtime.

Usage:
    python invoke.py "Read issue #1 and fix it from repo my-task-manager with user <your-git-user>"
    python invoke.py --session <id> "Now open a PR for that fix"
"""

import json
import os
import sys
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config() -> dict:
    config_path = os.path.join(SCRIPT_DIR, "runtime_config.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy-kiro.py first.")
        sys.exit(1)


def invoke(runtime_arn: str, prompt: str, region: str, session_id: str = None) -> dict:
    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(read_timeout=900),
    )

    if not session_id:
        session_id = str(uuid.uuid4())

    payload = json.dumps({"prompt": prompt}).encode("utf-8")

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=payload,
        )
    except ClientError as exc:
        print(f"  Session: {session_id}")
        print(f"  Error: {exc}")
        return {"_session_id": session_id}

    body = json.loads(response["response"].read().decode("utf-8"))
    runtime_session = response.get("runtimeSessionId", session_id)

    print(f"  Session: {runtime_session}")
    print(f"  Status:  {response.get('statusCode', 'N/A')}")

    body["_session_id"] = runtime_session
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

    if not args:
        print('Usage: python invoke.py [--session <id>] "<prompt>"')
        sys.exit(1)

    prompt = " ".join(args)

    print(f"Runtime: {runtime_arn}")
    if session_id:
        print(f"Resuming session: {session_id}")
    print(f"Prompt: {prompt}\n")

    result = invoke(runtime_arn, prompt, region, session_id)
    print(f"\nResponse:\n{result.get('response', result)}")

    sid = result.get("_session_id")
    if sid:
        print(f'\nTo continue: python invoke.py --session {sid} "<next prompt>"')


if __name__ == "__main__":
    main()
