"""Phase 3 / M1 end-to-end: a REAL receipt through the full agent pipeline.

Uploads the sample receipt to S3, invokes the deployed Runtime with {s3_uri,
user_id}, and asserts the agent OCR'd, extracted, and persisted an expense row to
DynamoDB (or routed it to review). No mocks. Requires a deployed stack (run via
`make e2e`); skips cleanly otherwise.
"""

import json
import os
import uuid

import boto3
import pytest
from boto3.dynamodb.conditions import Key

REGION = os.environ.get("AWS_REGION", "us-west-2")
STACK = os.environ.get("RECEIPTS_STACK", "AgentCore-ReceiptsAgent-dev")
EXPENSES_TABLE = os.environ.get("EXPENSES_TABLE", "ReceiptsAgent-Expenses")

pytestmark = pytest.mark.e2e


def _runtime_arn():
    cfn = boto3.client("cloudformation", region_name=REGION)
    try:
        outs = cfn.describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", [])
    except Exception:
        pytest.skip(f"stack {STACK} not deployed")
    for o in outs:
        if o["OutputKey"].startswith("RuntimeArn"):
            return o["OutputValue"]
    pytest.skip("RuntimeArn output not present")


def _upload_sample() -> str:
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    bucket = f"receipts-inbox-{account}-{REGION}"
    key = f"receipts/e2e-{uuid.uuid4().hex}.png"
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample-receipt.png")
    boto3.client("s3", region_name=REGION).upload_file(fixture, bucket, key)
    return f"s3://{bucket}/{key}"


def test_receipt_flows_through_agent_to_dynamodb():
    """The M1 milestone assertion: receipt in -> expense row out, via the agent."""
    s3_uri = _upload_sample()
    arn = _runtime_arn()
    client = boto3.client("bedrock-agentcore", region_name=REGION)

    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"m1-{uuid.uuid4().hex}",
        payload=json.dumps({"s3_uri": s3_uri, "user_id": "user-001"}).encode(),
    )
    raw = resp["response"]
    body = raw.read().decode() if hasattr(raw, "read") else raw
    data = json.loads(body) if isinstance(body, str) else body

    # The agent returns its outcome; status is processed or needs_review, never error.
    assert "error" not in data, f"agent returned error: {data}"
    assert data.get("status") in ("processed", "needs_review")
    # Order-independent: any valid rung (the ladder test may flip activeRung and
    # restores it, but don't hard-couple to L0).
    assert data.get("rung") in ("L0", "L1", "L2", "L3", "L4")
    expense = data.get("expense", {})
    assert expense.get("merchant"), "extractor should have produced a merchant"

    # Phase 4: the independent validator agent ran and owns the routing decision.
    validator = data.get("validator", {})
    assert validator.get("routing") in ("AUTO_PERSIST", "NEEDS_REVIEW"), (
        f"validator should have produced a routing decision, got: {validator}"
    )
    # Phase 5: status is "processed" ONLY when the validator approved AND Cedar did
    # not block at the gateway. A Cedar block (cedar_blocked) overrides an
    # AUTO_PERSIST into needs_review — the deterministic guardrail (spec §5.5).
    cedar_blocked = data.get("cedar_blocked", False)
    if data["status"] == "processed":
        assert validator["routing"] == "AUTO_PERSIST" and not cedar_blocked
    else:  # needs_review
        assert validator["routing"] == "NEEDS_REVIEW" or cedar_blocked
    # The deterministic table parser ran (the sample receipt has line items).
    assert "parse_rate" in data

    # And it really landed in DynamoDB under this user. Strongly-consistent read +
    # a short retry: the agent wrote the row moments ago, and a default
    # eventually-consistent query can miss a just-written item.
    import time

    table = boto3.resource("dynamodb", region_name=REGION).Table(EXPENSES_TABLE)
    found = False
    for _ in range(5):
        rows = table.query(KeyConditionExpression=Key("userId").eq("user-001"), ConsistentRead=True).get("Items", [])
        if any(s3_uri == r.get("sourceReceiptS3") for r in rows):
            found = True
            break
        time.sleep(2)
    assert found, (
        f"expense row for this receipt should exist in DynamoDB. "
        f"status={data.get('status')} cedar_blocked={data.get('cedar_blocked')} "
        f"tool_result={data.get('tool_result', '')[:300]}"
    )
