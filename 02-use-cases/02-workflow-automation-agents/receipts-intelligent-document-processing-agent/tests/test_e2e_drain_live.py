"""Phase 6 sub-step 3 end-to-end: the L4 SQS drain consumer (spec §12).

A receipt deferred at L4 was parked in SQS, not dropped. This proves the recovery
path: a message on the DeferQueue is picked up by the drain Lambda, which re-invokes
the Runtime, and the receipt completes (row lands in DynamoDB). The bounded drain
rate (reserved concurrency 1 + batch size 1 + jittered sleep) is what stops a
recovered tier from being stampeded — here we prove the backlog drains AT ALL and
the receipt isn't lost; the rate bound is structural (CDK), not something a single
message can exercise.

No mocks: real SQS, real drain Lambda, real Runtime, real DynamoDB.
Requires a deployed stack (run via `make e2e`); skips cleanly otherwise.
"""

import json
import os
import time
import uuid

import boto3
import pytest
from boto3.dynamodb.conditions import Key

REGION = os.environ.get("AWS_REGION", "us-west-2")
EXPENSES_TABLE = os.environ.get("EXPENSES_TABLE", "ReceiptsAgent-Expenses")
DEFER_QUEUE = "ReceiptsAgent-L4Defer"

pytestmark = pytest.mark.e2e


def _queue_url() -> str:
    sqs = boto3.client("sqs", region_name=REGION)
    try:
        return sqs.get_queue_url(QueueName=DEFER_QUEUE)["QueueUrl"]
    except Exception:
        pytest.skip(f"{DEFER_QUEUE} not deployed")


def _upload_sample() -> str:
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    bucket = f"receipts-inbox-{account}-{REGION}"
    key = f"receipts/drain-{uuid.uuid4().hex}.png"
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample-receipt.png")
    boto3.client("s3", region_name=REGION).upload_file(fixture, bucket, key)
    return f"s3://{bucket}/{key}"


def test_deferred_receipt_drains_from_sqs_and_completes():
    """A message on the L4 defer queue -> drain Lambda -> Runtime -> DynamoDB row."""
    queue_url = _queue_url()
    s3_uri = _upload_sample()
    user_id = f"drain-user-{uuid.uuid4().hex[:8]}"

    # Mimic the agent's L4 defer: a {s3_uri, user_id} message on the queue. The
    # drain Lambda's SQS event source picks it up and re-invokes the Runtime.
    boto3.client("sqs", region_name=REGION).send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({"s3_uri": s3_uri, "user_id": user_id, "deferred_at_rung": "L4"}),
    )

    # The drain paces itself (jittered) then runs a full pipeline (~90-100s). Poll
    # DynamoDB for the row keyed to this unique user + receipt.
    table = boto3.resource("dynamodb", region_name=REGION).Table(EXPENSES_TABLE)
    found = False
    for _ in range(30):  # up to ~5 min
        rows = table.query(KeyConditionExpression=Key("userId").eq(user_id), ConsistentRead=True).get("Items", [])
        if any(s3_uri == r.get("sourceReceiptS3") for r in rows):
            found = True
            break
        time.sleep(10)

    assert found, f"deferred receipt should have drained from SQS and produced an expense row for {user_id} ({s3_uri})"
