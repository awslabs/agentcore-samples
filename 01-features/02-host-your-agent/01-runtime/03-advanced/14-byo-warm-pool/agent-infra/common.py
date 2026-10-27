"""Shared helpers for the stub agent lifecycle (boto3 only, no AWS CLI)."""
import hashlib
import os
import pathlib
import subprocess
import sys
import time

import boto3

AGENT_NAME = os.environ.get("AGENT_NAME", "warm_pool_stub_agent")
EXECUTION_ROLE_NAME = f"{AGENT_NAME}-execution-role"
REPO_NAME = AGENT_NAME.replace("_", "-") + "-repo"
AGENT_DIR = pathlib.Path(__file__).resolve().parent.parent / "agent"
FAILED = {"CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"}


def clients():
    s = boto3.session.Session()
    if not s.region_name:
        sys.exit("set AWS_REGION")
    return {n: s.client(n) for n in ("bedrock-agentcore-control", "ecr", "iam", "sts")}


def log(msg):
    print(f"\033[1;34m==>\033[0m {msg}", file=sys.stderr, flush=True)


def run(cmd, stdin=None):
    log(" ".join(cmd[:4]) + (" ..." if len(cmd) > 4 else ""))
    subprocess.run(cmd, check=True, input=stdin, text=stdin is not None)


def find_runtime(c):
    acc = c["bedrock-agentcore-control"]
    kwargs = {}
    while True:
        resp = acc.list_agent_runtimes(**kwargs)
        for rt in resp.get("agentRuntimes", []):
            if rt["agentRuntimeName"] == AGENT_NAME:
                return rt
        if not resp.get("nextToken"):
            return None
        kwargs["nextToken"] = resp["nextToken"]


def wait_until_ready(c, runtime_id, tries=90, delay=10):
    acc = c["bedrock-agentcore-control"]
    for _ in range(tries):
        rt = acc.get_agent_runtime(agentRuntimeId=runtime_id)
        if rt["status"] == "READY":
            return rt
        if rt["status"] in FAILED:
            raise RuntimeError(f"runtime is {rt['status']}: {rt.get('failureReason', 'no reason given')}")
        log(f"runtime status: {rt['status']}")
        time.sleep(delay)
    raise TimeoutError(f"runtime {runtime_id} not READY after {tries * delay}s")


def image_tag():
    h = hashlib.sha256()
    for p in sorted(AGENT_DIR.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]
