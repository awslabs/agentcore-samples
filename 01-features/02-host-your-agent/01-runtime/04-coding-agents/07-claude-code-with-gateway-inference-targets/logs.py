"""
Print the last hour of agent log events for the deployed runtime.

    python logs.py [--region us-east-1]

Uses filter_log_events across the whole log group rather than reading the newest
streams: the runtime writes each instance's output to its own stream, and the stream
containing a turn is often not the most recently active one. Health-check pings are
filtered out because they arrive every few seconds and drown out the turn lines this
command exists to show.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

STATE_FILE = Path(__file__).with_name(".runtime-state.json")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()

    if not STATE_FILE.exists():
        sys.exit("no runtime state -- run `python deploy.py` first")
    state = json.loads(STATE_FILE.read_text())
    rid = next(
        (r.get("agent_runtime_id") for r in state["resources"] if r["kind"] == "agent_runtime"),
        None,
    )
    if not rid:
        sys.exit("no runtime in state -- run `python deploy.py` first")

    cw = boto3.client("logs", region_name=args.region)
    group = f"/aws/bedrock-agentcore/runtimes/{rid}-DEFAULT"
    print(f"=== {group} (last hour, pings filtered)")
    kwargs: dict[str, Any] = {
        "logGroupName": group,
        "startTime": int((time.time() - 3600) * 1000),
    }
    try:
        while True:
            resp = cw.filter_log_events(**kwargs)
            for e in resp.get("events", []):
                msg = e["message"].rstrip()
                if '"GET /ping HTTP/1.1" 200' in msg:
                    continue
                print("   ", msg)
            token = resp.get("nextToken")
            if not token:
                break
            kwargs["nextToken"] = token
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            print("    log group not found (no invocations yet?)")
        else:
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
