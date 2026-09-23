"""Phase 7 end-to-end: the event-driven front door (spec §13).

The Phase 7 DoD: dropping a receipt file in S3 — with NO direct Runtime invoke — runs
the whole pipeline. Proves S3 -> EventBridge -> trigger Lambda -> Runtime -> DynamoDB
end to end. No mocks; S3 PutObject is a real, immediate event (no 503/ingestion-lag
problem). Uses a per-user key (receipts/<user_id>/<file>) so the trigger derives the
user from the path AND the row is isolated from other tests' user-001 rows.

Requires a deployed stack (run via `make e2e`); skips cleanly otherwise.
"""

import os
import time
import uuid

import boto3
import pytest
from boto3.dynamodb.conditions import Key

REGION = os.environ.get("AWS_REGION", "us-west-2")
EXPENSES_TABLE = os.environ.get("EXPENSES_TABLE", "ReceiptsAgent-Expenses")

pytestmark = pytest.mark.e2e


def _inbox_bucket() -> str:
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    return f"receipts-inbox-{account}-{REGION}"


def test_s3_upload_runs_pipeline_via_front_door():
    """Drop a receipt in S3 (no direct invoke) -> expense row appears in DynamoDB."""
    bucket = _inbox_bucket()
    user_id = f"frontdoor-{uuid.uuid4().hex[:8]}"
    key = f"receipts/{user_id}/receipt-{uuid.uuid4().hex}.png"
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample-receipt.png")

    # The ONLY action: put the object. EventBridge + the trigger Lambda do the rest.
    boto3.client("s3", region_name=REGION).upload_file(fixture, bucket, key)
    s3_uri = f"s3://{bucket}/{key}"

    # Front door: EventBridge delivery + trigger Lambda + a full pipeline run
    # (~90-100s). Poll DynamoDB for the row under the key-derived user.
    table = boto3.resource("dynamodb", region_name=REGION).Table(EXPENSES_TABLE)
    found = False
    for _ in range(36):  # up to ~6 min
        rows = table.query(KeyConditionExpression=Key("userId").eq(user_id), ConsistentRead=True).get("Items", [])
        if any(s3_uri == r.get("sourceReceiptS3") for r in rows):
            found = True
            break
        time.sleep(10)

    assert found, (
        f"front door should have run the pipeline for {s3_uri} and written a row for "
        f"user {user_id} (S3->EventBridge->trigger->Runtime->DynamoDB)"
    )
