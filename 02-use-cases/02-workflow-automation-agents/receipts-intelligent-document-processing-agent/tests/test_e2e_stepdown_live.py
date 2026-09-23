"""Phase 6 sub-step 2 end-to-end: in-agent 503 step-down (spec §6.3).

Uses the env-gated fault-injection hook (ALLOW_FAULT_INJECTION=true on the Runtime)
to make the extractor's first model attempt fail with a simulated 503, and asserts
the agent stepped DOWN to the next rung's model for that run — no mocks of AWS, the
real agent doing the real step-down on a real receipt.

Requires a deployed stack (run via `make e2e`); skips cleanly otherwise.
"""

import json
import os
import uuid

import boto3
import pytest

REGION = os.environ.get("AWS_REGION", "us-west-2")
STACK = os.environ.get("RECEIPTS_STACK", "AgentCore-ReceiptsAgent-dev")

# DEFERRED (2026-06-23). The in-agent 503 step-down LOGIC is real and unit-tested
# (see tests/test_ladder.py: classify_model_error + next_rung, no mocks). What is
# NOT trustworthy is *live-simulating* a Bedrock 503: a real 503 is a capacity event
# we can't trigger on demand, and our env-gated fault-injection hook only proves the
# hook works, not that production behaves the same (it's effectively circular). The
# live run also surfaced a reporting inconsistency (rung stepped to L1 but the
# returned `model` field still read L0's model) worth investigating when revisited.
# Decision (with the user): keep the unit-verified logic, defer this live e2e until
# we either find a real fault-injection path or accept unit coverage as sufficient.
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skip(reason="deferred: can't faithfully simulate a live Bedrock 503; logic is unit-tested"),
]


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
    key = f"receipts/stepdown-{uuid.uuid4().hex}.png"
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample-receipt.png")
    boto3.client("s3", region_name=REGION).upload_file(fixture, bucket, key)
    return f"s3://{bucket}/{key}"


def test_persistent_503_steps_down_one_rung():
    """First model attempt 503s -> agent steps L0 -> L1 and completes on L1."""
    arn = _runtime_arn()
    s3_uri = _upload_sample()
    client = boto3.client("bedrock-agentcore", region_name=REGION)

    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=f"stepdown-{uuid.uuid4().hex}",
        # simulate one 503 on the first (L0) attempt; agent should step to L1.
        payload=json.dumps({"s3_uri": s3_uri, "user_id": "user-001", "simulate_503": 1}).encode(),
    )
    raw = resp["response"]
    body = raw.read().decode() if hasattr(raw, "read") else raw
    data = json.loads(body) if isinstance(body, str) else body

    assert "error" not in data, f"agent errored: {data}"
    steps = data.get("step_downs", [])
    assert len(steps) >= 1, f"expected a 503 step-down, got step_downs={steps} data={str(data)[:300]}"
    assert steps[0]["from"] == "L0" and steps[0]["to"] == "L1" and steps[0]["cause"] == "503"
    # The run finished on the stepped-down rung, on a DIFFERENT model than L0.
    assert data.get("rung") == "L1"
    assert data.get("model") == "global.anthropic.claude-opus-4-7"
