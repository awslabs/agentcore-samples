#!/usr/bin/env python3
"""Bounded model/E2E checks: 3 Runtime requests and one S3 event, no notifications.

Each uses a new policy ID. Correlate DynamoDB writes/reviews to that ID; never
accept model prose, an old row, or Trigger's initial HTTP 200 as completion.
Run Cedar/auth separately via test_cedar.py and test_auth.py.
"""

import argparse
import json
import time
import uuid

from botocore.config import Config
from cloud_checks import context


def rows_for(table, key, value):
    rows, kwargs = [], {"ConsistentRead": True, "Limit": 100}
    for _ in range(10):
        response = table.scan(**kwargs)
        rows.extend(item for item in response.get("Items", []) if item.get(key) == value)
        if not response.get("LastEvaluatedKey"):
            return rows
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    raise RuntimeError("Test table exceeds bounded scan")


def invoke(session, arn, prompt):
    client = session.client(
        "bedrock-agentcore", config=Config(connect_timeout=10, read_timeout=180, retries={"max_attempts": 0})
    )
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        qualifier="DEFAULT",
        runtimeSessionId=str(uuid.uuid4()),
        payload=json.dumps({"prompt": prompt}).encode(),
    )
    stream = response["response"]
    try:
        result = stream.read(512001)
        if len(result) > 512000:
            raise RuntimeError("Response exceeds bounded read")
        return result.decode()
    finally:
        stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-cloud", action="store_true")
    args = parser.parse_args()
    if not args.allow_cloud:
        parser.error("Explicit --allow-cloud required; these checks call models")
    state, session, outputs = context()
    prefix = f"{state['project']}-{state['target']}"
    dynamodb = session.resource("dynamodb")
    policies, claims, reviews = [dynamodb.Table(f"{prefix}-{name}") for name in ("Policies", "Claims", "Reviews")]
    cases = [
        (
            "approve",
            "active",
            2000,
            "A documented parking-lot fender bender, with photographs and a signed repair estimate.",
        ),
        (
            "review",
            "active",
            30000,
            "Something may have happened. I have no details, dates, photos or repair estimates.",
        ),
        ("reject", "expired", 500, "Minor bumper scratch."),
        (
            "event",
            "active",
            2000,
            "A documented parking-lot fender bender, with photographs and a signed repair estimate.",
        ),
    ]
    evidence = []
    for case, status, amount, description in cases:
        policy = "POL-TEST-" + uuid.uuid4().hex[:12]
        policies.put_item(
            Item={
                "policy_number": policy,
                "holder_name": "Offline Test Customer",
                "policy_type": "auto",
                "coverage_amount": 50000,
                "deductible": 500,
                "status": status,
            },
            ConditionExpression="attribute_not_exists(policy_number)",
        )
        prompt = f"File a claim under policy {policy}. Damage ${amount}. {description}"
        assert rows_for(claims, "policy_number", policy) == []
        if case == "event":
            bucket = f"{prefix.lower()}-inbox-{state['account']}-{state['region']}"
            key = f"claims-inbox/{policy}.eml"
            session.client("s3").put_object(
                Bucket=bucket, Key=key, Body=f"From: test@example.invalid\nSubject: {policy}\n\n{prompt}".encode()
            )
        else:
            response = invoke(session, outputs["RuntimeArn"], prompt)
            assert "Processing complete" in response, "Runtime did not complete Phase 3"
        rows = []
        for _ in range(36 if case == "event" else 1):
            rows = rows_for(claims, "policy_number", policy)
            if rows or case != "event":
                break
            time.sleep(5)
        if case == "reject":
            assert rows == [], rows
        else:
            assert len(rows) == 1, f"{case}: expected exactly one new claim for {policy}, got {rows}"
            expected = "pending_review" if case == "review" else "approved"
            assert rows[0]["status"] == expected, rows
            if case == "review":
                linked = rows_for(reviews, "claim_id", rows[0]["claim_id"])
                assert len(linked) == 1 and linked[0]["status"] == "pending", linked
        evidence.append({"case": case, "policy": policy, "claim_ids": [r["claim_id"] for r in rows]})
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
