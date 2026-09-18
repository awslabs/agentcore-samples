"""
Invoke the Claude Code agent deployed on AgentCore Runtime.

    python invoke.py "Reply with exactly the word: ok"
    python invoke.py --session-id <id> "Now list the files you created"

--session-id resumes the AgentCore Runtime session (the container), so a follow-up
prompt sees the workspace state the previous turn left behind.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

STATE_FILE = Path(__file__).with_name(".runtime-state.json")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("prompt")
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()

    if not STATE_FILE.exists():
        sys.exit("no runtime state -- run `python deploy.py` first")
    state = json.loads(STATE_FILE.read_text())
    arn = (state.get("outputs") or {}).get("agent_runtime_arn")
    if not arn:
        sys.exit("no runtime in state -- run `python deploy.py` first")

    # botocore defaults to a 60s read timeout and retries on timeout. An agent turn
    # easily exceeds 60s, and each retry re-enters /invocations and spawns ANOTHER
    # claude process, so a single logical request becomes several concurrent agent
    # runs. Long read timeout plus no retries keeps one invocation to one run.
    client = boto3.client(
        "bedrock-agentcore",
        region_name=args.region,
        config=Config(read_timeout=900, connect_timeout=15, retries={"max_attempts": 1}),
    )
    kwargs: dict[str, Any] = {
        "agentRuntimeArn": arn,
        "payload": json.dumps({"prompt": args.prompt}).encode(),
        "contentType": "application/json",
    }
    if args.session_id:
        kwargs["runtimeSessionId"] = args.session_id
    resp = client.invoke_agent_runtime(**kwargs)
    body = resp["response"].read() if hasattr(resp.get("response"), "read") else resp.get("response")
    print(body.decode() if isinstance(body, bytes) else body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
