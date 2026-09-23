"""L4 defer-queue drain consumer (spec §12 — the thundering-herd fix).

When the model tier was exhausted (L4), receipts were accepted and parked in SQS
instead of dropped (the agent's `_defer_receipt`). When a tier recovers, a naive
"process the whole backlog now" would slam the just-recovered model and re-trigger
the very `503` that filled the queue. This consumer drains the backlog at a
**bounded, jittered rate** so recovery is gradual by construction:

  - SQS event-source mapping with **batch size 1** + **reserved concurrency 1** (set
    in CDK) serializes the drain — at most one deferred receipt replays at a time.
  - A small **jittered sleep** before each re-invoke spreads load so a burst of
    recovered messages doesn't align into a spike.
  - Each message re-invokes the Runtime with its original `{s3_uri, user_id}`. If the
    ladder is still at L4, the agent simply re-defers (a fresh SQS message to the back
    of the queue) — so the backlog recirculates at the drain rate, never stampedes,
    and is never lost.

The drain invokes the Runtime as an AWS principal (IAM `InvokeAgentRuntime`), not via
the Gateway's M2M Cognito token — that token is for the agent's outbound tool calls.
"""

import json
import os
import random
import time
import uuid

import boto3

RUNTIME_ARN = os.environ.get("RUNTIME_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-west-2")
# Bounded, jittered pacing (the thundering-herd fix). Each replay waits a random
# interval in [DRAIN_MIN, DRAIN_MAX] seconds before re-invoking, so the recovered
# tier sees a trickle, not a wall. Concurrency=1 + batch=1 (CDK) does the rest.
DRAIN_MIN = float(os.environ.get("DRAIN_MIN_SECONDS", "1"))
DRAIN_MAX = float(os.environ.get("DRAIN_MAX_SECONDS", "3"))

_agentcore = boto3.client("bedrock-agentcore", region_name=REGION)


def _jitter() -> float:
    lo, hi = sorted((DRAIN_MIN, DRAIN_MAX))
    return random.uniform(lo, hi)  # nosec B311 — pacing jitter, not crypto


def _replay(body: dict) -> dict:
    """Re-invoke the Runtime with a previously deferred receipt."""
    payload = {"s3_uri": body.get("s3_uri"), "user_id": body.get("user_id", "anonymous")}
    resp = _agentcore.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        runtimeSessionId=f"drain-{uuid.uuid4().hex}",
        payload=json.dumps(payload).encode(),
    )
    raw = resp["response"]
    text = raw.read().decode() if hasattr(raw, "read") else raw
    return json.loads(text) if isinstance(text, str) else text


def handler(event, context):
    """SQS batch (size 1) of deferred receipts. Replay each at the bounded rate."""
    results = []
    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
        except (KeyError, ValueError):
            # Malformed message: let it fail so SQS redrives/DLQs it rather than
            # silently dropping a receipt.
            raise

        time.sleep(_jitter())  # pace the drain BEFORE hitting the model
        result = _replay(body)
        results.append({"s3_uri": body.get("s3_uri"), "status": result.get("status"), "rung": result.get("rung")})

    return {"drained": len(results), "results": results}
