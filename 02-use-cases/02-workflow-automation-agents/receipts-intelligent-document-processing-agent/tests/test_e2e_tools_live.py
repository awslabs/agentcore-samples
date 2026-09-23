"""Phase 2 end-to-end tests against REAL AWS — the five Gateway tool Lambdas.

Invokes each tool Lambda directly (real DynamoDB round-trip, no mocks) and checks
the Gateway exists with its five targets. Requires a deployed stack (run via
`make e2e`). Skips cleanly when resources aren't present so `make unit` stays green.
"""

import json
import os

import boto3
import pytest

REGION = os.environ.get("AWS_REGION", "us-west-2")
EXPENSES_TABLE = os.environ.get("EXPENSES_TABLE", "ReceiptsAgent-Expenses")

pytestmark = pytest.mark.e2e

_lambda = boto3.client("lambda", region_name=REGION)


def _invoke(fn_name: str, payload: dict) -> dict:
    try:
        resp = _lambda.invoke(FunctionName=fn_name, Payload=json.dumps(payload).encode())
    except _lambda.exceptions.ResourceNotFoundException:
        pytest.skip(f"{fn_name} not deployed")
    body = resp["Payload"].read().decode()
    # handlers return a JSON string; Lambda wraps it as a JSON string too
    out = json.loads(body)
    return json.loads(out) if isinstance(out, str) else out


def test_get_user_profile_reads_seeded_user():
    out = _invoke("ReceiptsAgent-GetUserProfile", {"user_id": "user-001"})
    assert out.get("userId") == "user-001"
    assert out.get("currency") == "USD"


def test_save_then_get_recent_round_trips():
    expense = {
        "user_id": "user-001",
        "merchant": "Blue Bottle Coffee",
        "transaction_date": "2026-06-23",
        "currency": "USD",
        "subtotal": 8.50,
        "tax": 0.75,
        "tip": 1.50,
        "total": 10.75,
        "category": "Meals & Entertainment",
        "line_items": [{"description": "Latte", "qty": 1, "unitPrice": 5.50, "amount": 5.50}],
        "rung": "L0",
    }
    saved = _invoke("ReceiptsAgent-SaveExpense", expense)
    assert saved.get("saved") is True
    expense_id = saved["expenseId"]

    recent = _invoke("ReceiptsAgent-GetRecentExpenses", {"user_id": "user-001"})
    assert recent["count"] >= 1
    assert any(e["expenseId"] == expense_id for e in recent["expenses"])


def test_save_expense_is_idempotent():
    """Same receipt content -> same expenseId -> no duplicate row (spec §8)."""
    expense = {"user_id": "user-001", "merchant": "Idem Cafe", "transaction_date": "2026-06-23", "total": 4.00}
    a = _invoke("ReceiptsAgent-SaveExpense", expense)
    b = _invoke("ReceiptsAgent-SaveExpense", expense)
    assert a["expenseId"] == b["expenseId"]


def test_lookup_merchant_passthrough_when_uncatalogued():
    out = _invoke("ReceiptsAgent-LookupMerchant", {"name": "Some New Diner #42"})
    assert out["matched"] is False
    assert out["merchant"]["merchantKey"] == "some-new-diner-42"


def test_human_review_records_needs_review():
    out = _invoke(
        "ReceiptsAgent-HumanReview",
        {"user_id": "user-001", "reason": "total did not reconcile", "merchant": "Blurry Receipt Co", "total": 99.0},
    )
    assert out.get("status") == "needs_review"
    # confirm it really landed with needs_review status
    table = boto3.resource("dynamodb", region_name=REGION).Table(EXPENSES_TABLE)
    item = table.get_item(Key={"userId": "user-001", "expenseId": out["expenseId"]}).get("Item")
    assert item and item["status"] == "needs_review"


def test_gateway_has_five_targets():
    """The Gateway exists and exposes all five tool targets."""
    cp = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gws = cp.list_gateways().get("items", [])
    gw = next((g for g in gws if "Receipts" in g.get("name", "")), None)
    if not gw:
        pytest.skip("ReceiptsGateway not found")
    targets = cp.list_gateway_targets(gatewayIdentifier=gw["gatewayId"]).get("items", [])
    assert len(targets) == 5, f"expected 5 targets, got {len(targets)}"
