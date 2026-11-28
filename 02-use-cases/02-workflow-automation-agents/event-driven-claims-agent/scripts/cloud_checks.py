#!/usr/bin/env python3
"""Bounded coordinator-only auth/Cedar checks. No emails, seeding, or model calls.

Uses only the exact IDs in .claims-v1.json. Never discover a pool by name.
"""

import argparse
import asyncio
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

import boto3
from lifecycle import ROOT, assert_owner, load, verify_auth
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def context():
    state = load(ROOT)
    if state["phase"] != "deployed" or state["notifications"] is not False:
        raise ValueError("Checks require the deployed notification-free test stack")
    session = boto3.Session(region_name=state["region"])
    if session.client("sts").get_caller_identity()["Account"] != state["account"]:
        raise ValueError("Account mismatch")
    verify_auth(state, session.client)
    stack = session.client("cloudformation").describe_stacks(StackName=state["stack_arn"])["Stacks"][0]
    assert_owner({t["Key"]: t["Value"] for t in stack.get("Tags", [])}, state)
    # A local flag alone is not evidence that a previously deployed stack is safe.
    for purpose in ("Notification", "HumanReview"):
        config = session.client("lambda").get_function_configuration(
            FunctionName=f"{state['project']}-{state['target']}-{purpose}"
        )
        if config.get("Environment", {}).get("Variables", {}).get("NOTIFICATIONS_ENABLED") != "false":
            raise ValueError("Deployed notification mode is not OFF; refusing test effects")
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}
    return state, session, outputs


def token(state, session, scope="agentcore/invoke"):
    client = session.client("cognito-idp").describe_user_pool_client(
        UserPoolId=state["pool_id"], ClientId=state["client_id"]
    )["UserPoolClient"]
    basic = base64.b64encode(f"{state['client_id']}:{client['ClientSecret']}".encode()).decode()
    url = f"https://{state['domain']}.auth.{state['region']}.amazoncognito.com/oauth2/token"
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": scope}).encode(),
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())["access_token"]


def negative_http(url, headers, payload):
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers}
            ),
            timeout=15,
        ) as response:
            raise AssertionError(f"Expected authentication rejection, got {response.status}")
    except urllib.error.HTTPError as error:
        if error.code not in (401, 403):
            raise AssertionError(f"Unexpected HTTP {error.code}, not authentication rejection") from error


async def gateway_session(url, bearer, check):
    async with (
        streamablehttp_client(url, headers={"Authorization": f"Bearer {bearer}"}, timeout=30) as (read, write, _),
        ClientSession(read, write) as mcp,
    ):
        await mcp.initialize()
        return await check(mcp)


def auth_checks(state, session, outputs):
    bearer = token(state, session)
    runtime = outputs["RuntimeArn"]
    url = f"https://bedrock-agentcore.{state['region']}.amazonaws.com/runtimes/{urllib.parse.quote(runtime, safe='')}/invocations"
    for headers in ({}, {"Authorization": f"Bearer {bearer}"}, {"Authorization": "Bearer invalid"}):
        negative_http(url, headers, {"prompt": "auth-negative; must never reach a model"})
    for headers in ({}, {"Authorization": "Bearer invalid"}):
        negative_http(outputs["GatewayUrl"], headers, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    try:
        token(state, session, scope="nonexistent/scope")
    except urllib.error.HTTPError as error:
        body = json.loads(error.read())
        if error.code != 400 or body.get("error") != "invalid_scope":
            raise AssertionError("Expected Cognito invalid_scope, not any token error") from error
    else:
        raise AssertionError("Cognito issued a token for an unconfigured scope")

    async def list_tools(mcp):
        names = {t.name for t in (await mcp.list_tools()).tools}
        assert "create-claim___create_claim" in names, names
        return sorted(names)

    names = asyncio.run(gateway_session(outputs["GatewayUrl"], bearer, list_tools))
    print(
        json.dumps(
            {
                "auth": "passed",
                "discovered_tools": names,
                "runtime_iam_positive": "NOT RUN (requires bounded model invocation separately)",
            }
        )
    )


def matching_rows(table, marker):
    rows, kwargs = [], {"ConsistentRead": True, "Limit": 100}
    for _ in range(10):
        response = table.scan(**kwargs)
        rows.extend(item for item in response.get("Items", []) if item.get("description") == marker)
        if not response.get("LastEvaluatedKey"):
            return rows
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    raise RuntimeError("Test table exceeds bounded scan; no denial assertion possible")


def cedar_checks(state, session, outputs):
    table = session.resource("dynamodb").Table(f"{state['project']}-{state['target']}-Claims")
    bearer = token(state, session)

    async def run(mcp):
        discovered = {t.name for t in (await mcp.list_tools()).tools}
        name = "create-claim___create_claim"
        assert name in discovered
        evidence = []
        for amount in (99999, 100000):
            marker = "cedar-check-" + uuid.uuid4().hex
            assert matching_rows(table, marker) == []
            result = await mcp.call_tool(
                name,
                arguments={
                    "policy_number": "OFFLINE-TEST-POLICY",
                    "description": marker,
                    "estimated_amount": amount,
                    "category": "auto",
                    "status": "pending_review",
                    "decision": "test",
                },
            )
            text = "\n".join(b.text for b in result.content if hasattr(b, "text"))
            rows = matching_rows(table, marker)
            if amount < 100000:
                assert not result.isError, text
                payload = json.loads(text)
                assert len(rows) == 1 and rows[0]["claim_id"] == payload["claim_id"], rows
            else:
                assert result.isError, "Gateway did not signal a tool error"
                assert any(
                    s in text.lower() for s in ("not authorized", "access denied", "policy evaluation", "forbidden")
                ), text
                assert rows == [], "Denied call caused a DynamoDB write"
            evidence.append(
                {
                    "amount": amount,
                    "marker": marker,
                    "is_error": bool(result.isError),
                    "matching_rows": len(rows),
                    "claim_ids": [r["claim_id"] for r in rows],
                }
            )
        return evidence

    print(json.dumps(asyncio.run(gateway_session(outputs["GatewayUrl"], bearer, run)), indent=2))


def main(operation=None):
    parser = argparse.ArgumentParser(description=__doc__)
    if operation is None:
        parser.add_argument("operation", choices=("auth", "cedar"))
    parser.add_argument("--allow-cloud", action="store_true")
    args = parser.parse_args()
    if not args.allow_cloud:
        parser.error("Explicit --allow-cloud required")
    state, session, outputs = context()
    {"auth": auth_checks, "cedar": cedar_checks}[operation or args.operation](state, session, outputs)


if __name__ == "__main__":
    main()
