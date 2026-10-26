"""Redriving failed memory extraction jobs.

What you learn:
    - ListMemoryExtractionJobs to find jobs that failed
    - StartMemoryExtractionJob to redrive a job by id
    - When a redrive is appropriate vs. when to investigate first

Long-term extraction runs asynchronously after CreateEvent. If a job fails
(model throttle, transient error, validation issue), AgentCore records it
as an extraction job with status=FAILED and a failureReason. You can list
those, decide whether the underlying issue is fixed, and redrive.

Two surfaces:
    python redrive-failed-extractions.py boto3
    python redrive-failed-extractions.py sdk

Prerequisites:
    pip install boto3 bedrock-agentcore
    export AWS_REGION=us-east-1   # use any AgentCore-supported region
    export MEMORY_ID=<memory-id-with-failed-jobs>
"""

import os
import sys
import time

REGION = os.getenv("AWS_REGION", "us-east-1")


# === boto3 ============================================================
def run_with_boto3() -> None:
    import boto3

    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        print("[boto3] Set MEMORY_ID to a memory resource with failed extraction jobs.")
        return

    data = boto3.client("bedrock-agentcore", region_name=REGION)

    failed = []
    next_token = None
    while True:
        kwargs = {"memoryId": memory_id, "filter": {"status": "FAILED"}}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = data.list_memory_extraction_jobs(**kwargs)
        failed.extend(resp.get("jobs", []))
        next_token = resp.get("nextToken")
        if not next_token:
            break

    print(f"[boto3] Found {len(failed)} failed job(s) for {memory_id}")
    for j in failed:
        print(
            f"  jobId={j['jobID']} actor={j.get('actorId')} session={j.get('sessionId')} strategy={j.get('strategyId')}"
        )
        print(f"    failureReason={j.get('failureReason')}")

    # Gate redrive on a deliberate fix — blind retries waste tokens.
    for j in failed:
        echoed = data.start_memory_extraction_job(
            memoryId=memory_id,
            extractionJob={"jobId": j["jobID"]},
        )["jobId"]
        print(f"[boto3] Redrove jobId={echoed}")
        time.sleep(1)


# === AgentCore SDK ====================================================
def run_with_sdk() -> None:
    from bedrock_agentcore.memory import MemoryClient

    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        print("[sdk] Set MEMORY_ID to a memory resource with failed extraction jobs.")
        return

    client = MemoryClient(region_name=REGION)

    failed = []
    next_token = None
    while True:
        kwargs = {"memoryId": memory_id, "filter": {"status": "FAILED"}}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = client.list_memory_extraction_jobs(**kwargs)
        failed.extend(resp.get("jobs", []))
        next_token = resp.get("nextToken")
        if not next_token:
            break

    print(f"[sdk] Found {len(failed)} failed job(s) for {memory_id}")
    for j in failed:
        print(
            f"  jobId={j['jobID']} actor={j.get('actorId')} session={j.get('sessionId')} strategy={j.get('strategyId')}"
        )
        print(f"    failureReason={j.get('failureReason')}")

    # Gate redrive on a deliberate fix — blind retries waste tokens.
    for j in failed:
        echoed = client.start_memory_extraction_job(
            memoryId=memory_id,
            extractionJob={"jobId": j["jobID"]},
        )["jobId"]
        print(f"[sdk] Redrove jobId={echoed}")
        time.sleep(1)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--cleanup"]
    surface = args[0] if args else "boto3"
    if surface == "boto3":
        run_with_boto3()
    elif surface == "sdk":
        run_with_sdk()
    else:
        print(f"Unknown surface {surface!r}. Use boto3 | sdk.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
