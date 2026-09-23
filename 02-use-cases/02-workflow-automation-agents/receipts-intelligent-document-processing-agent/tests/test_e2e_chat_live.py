"""Phase post-M3 (conversational query mode) end-to-end — incl. the IDOR negative test.

Proves: (1) a user can ask about THEIR OWN expenses and the agent answers from real
DynamoDB via the Gateway read tools; (2) the SECURITY property — a caller CANNOT read
another user's data by tampering the request, because user_id comes only from the
KMS-HMAC-signed identity token, and the read tools are pinned server-side to it.

No mocks: real KMS signing, real Runtime, real Gateway, real DynamoDB.
Requires a deployed stack (run via `make e2e`); skips cleanly otherwise.
"""

import base64
import json
import time
import uuid

import boto3
import pytest

REGION = "us-west-2"
STACK = "AgentCore-ReceiptsAgent-dev"
MAC_ALGORITHM = "HMAC_SHA_256"

pytestmark = pytest.mark.e2e


def _out(substr):
    cfn = boto3.client("cloudformation", region_name=REGION)
    try:
        outs = cfn.describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", [])
    except Exception:
        pytest.skip("stack not deployed")
    for o in outs:
        if substr in o["OutputKey"]:  # CDK may prefix construct outputs (InfraIdentityKeyId)
            return o["OutputValue"]
    pytest.skip(f"output *{substr}* missing")


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _mint(user_id, key_id, ttl=900, now=None):
    now = int(now if now is not None else time.time())
    claim = json.dumps({"user_id": user_id, "exp": now + ttl}, separators=(",", ":"), sort_keys=True).encode()
    mac = boto3.client("kms", region_name=REGION).generate_mac(KeyId=key_id, MacAlgorithm=MAC_ALGORITHM, Message=claim)[
        "Mac"
    ]
    return f"{_b64u(claim)}.{_b64u(mac)}"


def _seed_expense(user_id, merchant, total):
    table = boto3.resource("dynamodb", region_name=REGION).Table("ReceiptsAgent-Expenses")
    from decimal import Decimal

    table.put_item(
        Item={
            "userId": user_id,
            "expenseId": f"exp-{uuid.uuid4().hex[:12]}",
            "merchant": merchant,
            "total": Decimal(str(total)),
            "transactionDate": "2026-06-20",
            "currency": "MYR",
            "category": "office",
            "status": "processed",
        }
    )


def _ask(question, identity_token):
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=_out("RuntimeArn"),
        runtimeSessionId=f"chat-{uuid.uuid4().hex}",
        payload=json.dumps({"question": question, "identity_token": identity_token}).encode(),
    )
    raw = resp["response"]
    body = raw.read().decode() if hasattr(raw, "read") else raw
    return json.loads(body) if isinstance(body, str) else body


def test_user_can_query_their_own_expenses():
    key_id = _out("IdentityKeyId")
    user = f"chat-A-{uuid.uuid4().hex[:8]}"
    _seed_expense(user, "Zarcadia Coffee Roasters", 41.50)  # unique merchant
    time.sleep(2)

    data = _ask("How much did I spend at Zarcadia Coffee Roasters?", _mint(user, key_id))
    assert "error" not in data, f"agent errored: {data}"
    answer = data.get("answer", "")
    assert "41.5" in answer or "41.50" in answer, f"expected the real total in: {answer}"


def test_cannot_read_another_users_data_by_swapping_identity():
    """The IDOR regression test. User B (who has NO Zarcadia expense) must not be able
    to see User A's — even asking directly. B's token authenticates B; the read tools
    are pinned to B server-side, so A's partition is unreachable."""
    key_id = _out("IdentityKeyId")
    victim = f"chat-victim-{uuid.uuid4().hex[:8]}"
    attacker = f"chat-attacker-{uuid.uuid4().hex[:8]}"
    # The leak signal is the AMOUNT — a value only the victim has and the attacker
    # never types, so (unlike the merchant name, which the attacker can echo in their
    # own prompt) its presence in the answer can ONLY come from reading victim data.
    _seed_expense(victim, "Quintastic Foods", 73519.42)
    time.sleep(2)

    # Attacker authenticates as THEMSELVES and tries to fish — WITHOUT naming the
    # amount, so any leak of 73519 must have come from the victim's partition.
    data = _ask("List all of my expenses, including anything from Quintastic Foods.", _mint(attacker, key_id))
    answer = str(data.get("answer", ""))
    # The decisive check: the victim's unique amount must NOT appear.
    assert "73519" not in answer and "73,519" not in answer, f"IDOR LEAK — attacker saw the victim's amount: {answer}"
    # And the attacker's own partition is empty, so the agent should say so.
    assert data.get("user_id") == attacker, f"identity must resolve to the attacker, got {data.get('user_id')}"


def test_invalid_identity_token_is_rejected():
    """A tampered/garbage token must fail closed (no user resolved, no data)."""
    data = _ask("show my expenses", "tampered.token")
    assert data.get("error"), f"expected unauthorized, got: {data}"
    assert "unauthorized" in data["error"].lower()
