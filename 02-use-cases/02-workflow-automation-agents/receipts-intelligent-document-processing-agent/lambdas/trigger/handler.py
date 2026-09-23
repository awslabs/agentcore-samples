"""Trigger Lambda: S3 receipt upload -> EventBridge -> invoke the Runtime (spec §13).

The event-driven front door. A receipt lands in the inbox bucket; S3 emits an
"Object Created" event; an EventBridge rule fires this Lambda, which builds the
agent payload ({s3_uri, user_id}) and invokes the Runtime. There is NO logged-in
user at this point — the agent authenticates as itself (agent-as-principal, spec
§10), and this Lambda invokes the Runtime with its own IAM credentials
(bedrock-agentcore:InvokeAgentRuntime, granted by CDK).

Invoke path: boto3 `invoke_agent_runtime` (the same call the L4 drain consumer uses
and that the live e2e already proved). The claims sample signs a raw HTTPS request
with SigV4; boto3 does that for us, so we use it for consistency with the drain path.

user_id convention: a key `receipts/<user_id>/<file>` carries the user in the path;
a flat key `receipts/<file>` (or anything without a user segment) falls back to the
seeded default user. This keeps simple uploads working and supports per-user inboxes.
"""

import json
import os
import uuid

import boto3

RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-west-2")
DEFAULT_USER_ID = os.environ.get("DEFAULT_USER_ID", "user-001")

_agentcore = boto3.client("bedrock-agentcore", region_name=REGION)


def user_id_from_key(key: str) -> str:
    """Derive the user id from the S3 key. `receipts/<user_id>/<file>` -> <user_id>;
    a flat `receipts/<file>` (or any key without a user segment) -> the default user.
    Pure (no AWS) so it is unit-testable."""
    parts = [p for p in key.split("/") if p]
    # parts[0] is the prefix ("receipts"); a user segment sits between it and the file.
    if len(parts) >= 3:
        return parts[1]
    return DEFAULT_USER_ID


def build_payload(bucket: str, key: str) -> dict:
    """Build the agent invoke payload from an S3 object location. Pure/unit-testable."""
    return {"s3_uri": f"s3://{bucket}/{key}", "user_id": user_id_from_key(key)}


def _invoke_runtime(payload: dict) -> dict:
    resp = _agentcore.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        runtimeSessionId=f"frontdoor-{uuid.uuid4().hex}",
        payload=json.dumps(payload).encode(),
    )
    raw = resp["response"]
    text = raw.read().decode() if hasattr(raw, "read") else raw
    return json.loads(text) if isinstance(text, str) else text


def handler(event, context):
    """EventBridge S3 "Object Created" -> invoke the Runtime with {s3_uri, user_id}."""
    detail = event.get("detail", {}) if isinstance(event, dict) else {}
    bucket = detail.get("bucket", {}).get("name", "")
    key = detail.get("object", {}).get("key", "")
    if not bucket or not key:
        return {"statusCode": 400, "body": "missing S3 event details", "event": str(event)[:500]}

    payload = build_payload(bucket, key)
    result = _invoke_runtime(payload)
    # A failed invoke raises -> the Lambda retries (CDK retryAttempts) -> DLQ. We do
    # not swallow it: a dropped receipt must be visible, never silently lost.
    print(f"front door: {payload['s3_uri']} -> status={result.get('status')} rung={result.get('rung')}")
    return {
        "statusCode": 200,
        "s3_uri": payload["s3_uri"],
        "user_id": payload["user_id"],
        "status": result.get("status"),
        "rung": result.get("rung"),
    }
