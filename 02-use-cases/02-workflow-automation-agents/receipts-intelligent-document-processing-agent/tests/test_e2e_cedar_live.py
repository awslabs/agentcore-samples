"""Phase 5 end-to-end: Cedar policy enforcement at the Gateway (no mocks).

Cedar runs at the GATEWAY, not in the Lambda — so this drives a real MCP tool call
through the gateway with the agent's own M2M token, deterministically (no LLM):
  - save_expense with total >= $2000  -> DENIED by BlockExcessiveExpense
  - save_expense with a small total   -> ALLOWED (AllowAllTools)

Requires a deployed stack (run via `make e2e`); skips cleanly otherwise.
"""

import base64
import json
import os
import urllib.parse
import urllib.request
import uuid

import boto3
import pytest

REGION = os.environ.get("AWS_REGION", "us-west-2")
STACK = os.environ.get("RECEIPTS_STACK", "AgentCore-ReceiptsAgent-dev")

pytestmark = pytest.mark.e2e


def _outputs():
    cfn = boto3.client("cloudformation", region_name=REGION)
    try:
        outs = cfn.describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", [])
    except Exception:
        pytest.skip(f"stack {STACK} not deployed")
    return {o["OutputKey"]: o["OutputValue"] for o in outs}


def _find(outs, prefix):
    for k, v in outs.items():
        if k.startswith(prefix):
            return v
    return ""


def _m2m_token_and_gateway():
    outs = _outputs()
    user_pool_id = _find(outs, "InfraUserPoolId")
    client_id = _find(outs, "InfraUserPoolClientId")
    if not user_pool_id or not client_id:
        pytest.skip("Cognito outputs not present")

    cog = boto3.client("cognito-idp", region_name=REGION)
    secret = cog.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)["UserPoolClient"][
        "ClientSecret"
    ]
    domain = cog.describe_user_pool(UserPoolId=user_pool_id)["UserPool"].get("Domain", "")
    token_endpoint = f"https://{domain}.auth.{REGION}.amazoncognito.com/oauth2/token"

    creds = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "agentcore/invoke"}).encode()
    req = urllib.request.Request(
        token_endpoint,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
        token = json.loads(resp.read())["access_token"]

    # Gateway MCP URL from the control plane.
    cp = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gw = next((g for g in cp.list_gateways().get("items", []) if "Receipts" in g.get("name", "")), None)
    if not gw:
        pytest.skip("ReceiptsGateway not found")
    gw_full = cp.get_gateway(gatewayIdentifier=gw["gatewayId"])
    url = gw_full.get("gatewayUrl") or gw_full.get("gatewayEndpoint")
    return token, url


def _call_save_expense(total: float) -> "tuple[bool, str]":
    """Call save_expense through the gateway over MCP. Returns (denied, raw)."""
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient

    token, url = _m2m_token_and_gateway()
    client = MCPClient(lambda: streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}))
    args = {
        "user_id": "user-001",
        "merchant": f"Cedar Test {uuid.uuid4().hex[:6]}",
        "transaction_date": "2026-06-23",
        "currency": "USD",
        "total": total,
        "category": "Meals & Entertainment",
    }
    with client as gw:
        tools = gw.list_tools_sync()
        name = next(
            (t.tool_name for t in tools if "save_expense" in t.tool_name or t.tool_name.endswith("save-expense")),
            "save-expense",
        )
        result = gw.call_tool_sync(tool_use_id=uuid.uuid4().hex, name=name, arguments=args)
    raw = json.dumps(result, default=str).lower()
    status = result.get("status") if isinstance(result, dict) else getattr(result, "status", None)
    denied = status == "error" or any(k in raw for k in ("denied", "not authorized", "forbidden", "policy"))
    return denied, raw


def test_over_threshold_save_is_denied_by_cedar():
    # Integer total to isolate the int-vs-decimal Cedar typing question.
    denied, raw = _call_save_expense(5000)
    assert denied, f"$5000 save_expense should be DENIED by BlockExcessiveExpense; got: {raw[:400]}"


def test_under_threshold_save_is_allowed():
    denied, raw = _call_save_expense(12)
    assert not denied, f"$12 save_expense should be ALLOWED; got: {raw[:400]}"


def _call_tool(tool_suffix: str, args: dict) -> "tuple[bool, str]":
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient

    token, url = _m2m_token_and_gateway()
    client = MCPClient(lambda: streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}))
    with client as gw:
        tools = gw.list_tools_sync()
        name = next(
            (
                t.tool_name
                for t in tools
                if tool_suffix in t.tool_name or t.tool_name.endswith(tool_suffix.replace("_", "-"))
            ),
            tool_suffix,
        )
        result = gw.call_tool_sync(tool_use_id=uuid.uuid4().hex, name=name, arguments=args)
    raw = json.dumps(result, default=str).lower()
    status = result.get("status") if isinstance(result, dict) else getattr(result, "status", None)
    denied = status == "error" or any(k in raw for k in ("denied", "not authorized", "forbidden", "policy"))
    return denied, raw


def test_human_review_is_allowed_even_over_threshold():
    """The safe sink must NEVER be blocked — a high-total human_review (which the
    agent falls back to when save is blocked) must be allowed, or blocked saves
    would have nowhere to go. Guards the over-broad-policy regression."""
    denied, raw = _call_tool(
        "human_review",
        {"user_id": "user-001", "reason": "over threshold", "merchant": "Big Co", "total": 9999},
    )
    assert not denied, f"human_review must be ALLOWED even at total=9999; got: {raw[:400]}"
