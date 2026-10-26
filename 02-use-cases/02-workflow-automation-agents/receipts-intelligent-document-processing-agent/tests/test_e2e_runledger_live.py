"""Phase post-M3 (operational audit) end-to-end: the run ledger.

Proves the scalability fix: a processed receipt produces a ProcessingRuns row keyed by
receiptId=hash(s3_uri), reachable in ONE GetItem — the replacement for the
cross-three-log-groups forensic dig. The full path runs for real: agent fires an
EventBridge event -> writer Lambda -> DynamoDB. No mocks.

Requires a deployed stack (run via `make e2e`); skips cleanly otherwise.
"""

import hashlib
import json
import os
import time
import uuid

import boto3
import pytest

REGION = os.environ.get("AWS_REGION", "us-west-2")
RUNS_TABLE = os.environ.get("RUNS_TABLE", "ReceiptsAgent-ProcessingRuns")

pytestmark = pytest.mark.e2e


def _receipt_id(s3_uri: str) -> str:
    return "rcpt-" + hashlib.sha256(s3_uri.encode()).hexdigest()[:16]


def _runtime_arn():
    cfn = boto3.client("cloudformation", region_name=REGION)
    try:
        outs = cfn.describe_stacks(StackName="AgentCore-ReceiptsAgent-dev")["Stacks"][0].get("Outputs", [])
    except Exception:
        pytest.skip("stack not deployed")
    for o in outs:
        if o["OutputKey"].startswith("RuntimeArn"):
            return o["OutputValue"]
    pytest.skip("RuntimeArn output not present")


def test_processed_receipt_lands_in_run_ledger():
    """Invoke on a real receipt -> a ProcessingRuns row appears for that receiptId."""
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    bucket = f"receipts-inbox-{account}-{REGION}"
    key = f"receipts/ledger-{uuid.uuid4().hex}.png"
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample-receipt.png")
    boto3.client("s3", region_name=REGION).upload_file(fixture, bucket, key)
    s3_uri = f"s3://{bucket}/{key}"

    # Invoke directly (deterministic — not waiting on the S3->trigger hop here).
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    client.invoke_agent_runtime(
        agentRuntimeArn=_runtime_arn(),
        runtimeSessionId=f"ledger-{uuid.uuid4().hex}",
        payload=json.dumps({"s3_uri": s3_uri, "user_id": "ledger-user"}).encode(),
    )

    # The agent fires the event after returning; the writer Lambda upserts async.
    table = boto3.resource("dynamodb", region_name=REGION).Table(RUNS_TABLE)
    rid = _receipt_id(s3_uri)
    row = None
    for _ in range(18):  # up to ~90s
        row = table.get_item(Key={"receiptId": rid}).get("Item")
        if row:
            break
        time.sleep(5)

    assert row, f"no ProcessingRuns row for {s3_uri} (receiptId={rid})"
    assert row["s3Uri"] == s3_uri
    assert row["status"] in ("processed", "needs_review")  # real fate, recorded
    assert row.get("rung") in ("L0", "L1", "L2", "L3", "L4")
    assert "processedAt" in row
