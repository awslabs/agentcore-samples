"""
Invoke both agents from this sample and compare the machines they land on.

Run `python deploy.py` first — it writes cp_config.json, which this reads.

    python invoke.py                      # both runtimes, whoami, timings
    python invoke.py --only zip           # just one of them
    python invoke.py --prompt "run df -h"  # ask something else

WHAT TO WATCH FOR
-----------------
1. The FIRST invoke against a runtime session is slow — minutes, not seconds.
   A CapacityProvider has to launch an EC2 instance, boot it, and seed your
   artifact onto it before your agent sees the request. Every invoke after
   that, on the same session, is single-digit seconds.
2. Both runtimes report the same architecture, CPU count and memory, because
   both are bound to the same CapacityProvider. Only `artifact_kind` differs.
   Artifact type (zip vs container) and compute (the CP) are orthogonal.
3. Each runtime session gets its own instance. Two sessions, two hostnames.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "cp_config.json"

DEFAULT_PROMPT = "Use the whoami tool and report exactly what it returns."

# InvokeAgentRuntime requires a session id of at least 33 characters.
SESSION_MIN_LEN = 33


def data_client(region: str | None):
    """
    The data-plane client, straight from boto3 — but not with default settings.

    The long read timeout is NOT optional. A cold invoke waits for an EC2 instance
    to be launched, booted and seeded with your artifact before your agent sees
    the request; we measured 8 minutes on a brand-new CapacityProvider (see
    README → Cold start). botocore's default read timeout is 60s, which would
    abort a perfectly healthy cold start.

    Retries are disabled on purpose: InvokeAgentRuntime is not idempotent, so a
    botocore-level retry during a slow cold start would open a SECOND session on
    a SECOND instance. The retry loop in `invoke()` below is the deliberate one —
    it reuses the same session id.
    """
    region = (
        region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or boto3.Session().region_name
    )
    if not region:
        sys.exit("No AWS region configured. Set AWS_REGION.")
    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            read_timeout=900,
            connect_timeout=30,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )


def new_session_id(kind: str) -> str:
    sid = f"basichttp-{kind}-{uuid.uuid4().hex}"
    return sid.ljust(SESSION_MIN_LEN, "0")[:64]


def extract_text(body: str) -> str:
    """Pull the assistant text out of the agent's JSON response."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:4000]

    result = payload.get("result", payload)
    # Strands returns a message dict: {"role": ..., "content": [{"text": ...}]}
    if isinstance(result, dict) and "content" in result:
        parts = [
            block["text"] for block in result["content"] if isinstance(block, dict) and "text" in block
        ]
        if parts:
            return "\n".join(parts)
    return json.dumps(result, indent=2, default=str)


def invoke(data, arn: str, session_id: str, prompt: str, attempts: int = 5) -> tuple[str, float]:
    """
    Invoke the agent, retrying the transient `InternalServerException`
    (see README → "Transient errors on invoke").

    Those failures return in 1–3 seconds, before any instance is launched. A
    slow call is a genuine cold start and is never interrupted. Retries reuse
    the same session id so we stay pinned to one instance.
    """
    started = time.time()
    last = None
    for attempt in range(1, attempts + 1):
        try:
            response = data.invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                payload=json.dumps({"prompt": prompt}).encode(),
                contentType="application/json",
            )
            body = response["response"].read().decode()
            return extract_text(body), time.time() - started
        except Exception as exc:  # noqa: BLE001 — transient service-side errors
            name = type(exc).__name__
            if "InternalServerException" not in name and "RuntimeClientError" not in name:
                raise
            last = exc
            if attempt < attempts:
                print(f"    ({name} — transient, retry {attempt}/{attempts - 1})",
                      flush=True)
                time.sleep(15)
    raise last


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["zip", "container"], help="invoke just one artifact")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--repeat",
        type=int,
        default=2,
        help="invokes per runtime — the first is cold, the rest warm (default: 2)",
    )
    args = parser.parse_args()

    if not CONFIG.is_file():
        sys.exit(f"{CONFIG.name} not found — run `python deploy.py` first.")
    config = json.loads(CONFIG.read_text())

    runtimes = config["runtimes"]
    if args.only:
        if args.only not in runtimes:
            sys.exit(f"No {args.only!r} runtime in {CONFIG.name}. Deployed: {sorted(runtimes)}")
        runtimes = {args.only: runtimes[args.only]}

    data = data_client(config.get("region"))

    print(f"CapacityProvider: {config['capacityProviderId']}")
    print(f"Prompt:           {args.prompt}\n")

    for kind, runtime in runtimes.items():
        session_id = new_session_id(kind)
        print("=" * 72)
        print(f"{kind.upper()}  {runtime['arn'].split('/')[-1]}")
        print(f"session {session_id}")
        print("=" * 72)

        for attempt in range(1, args.repeat + 1):
            label = "cold" if attempt == 1 else f"warm #{attempt - 1}"
            if attempt == 1:
                print(f"[{label}] invoking — this can take several minutes, be patient...", flush=True)
            text, elapsed = invoke(data, runtime["arn"], session_id, args.prompt)
            print(f"\n[{label}] {elapsed:.1f}s")
            print(text)
        print()

    if len(runtimes) > 1:
        print("Both agents ran the SAME agent.py, on the SAME CapacityProvider.")
        print("Only `artifact_kind` differs — one arrived as a zip, one as a container image.")


if __name__ == "__main__":
    main()
