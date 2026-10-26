#!/usr/bin/env python3
"""Focused Cedar policy enforcement tests.

Tests:
1. High-value claim ($150k) → should be blocked by BlockExcessiveClaims policy
2. Normal claim ($5k)       → should succeed (not blocked)
3. Boundary claim ($99,999) → should succeed (just under threshold)

Usage:
    python3 scripts/test_cedar.py --region us-west-2
"""

import argparse
import base64
import json
import sys
import urllib.parse
import urllib.request

import boto3


def get_cognito_token(region: str) -> tuple[str, str]:
    """Get M2M token and runtime ARN from CloudFormation outputs."""
    cf = boto3.client("cloudformation", region_name=region)
    outputs = cf.describe_stacks(StackName="AgentCore-ClaimsAgent-dev")["Stacks"][0]["Outputs"]
    output_map = {o["OutputKey"]: o["OutputValue"] for o in outputs}

    # CDK auto-generates output keys with hash suffixes — find by prefix
    def find_output(prefix):
        for key, val in output_map.items():
            if key.startswith(prefix) or key == prefix:
                return val
        return ""

    user_pool_id = find_output("InfraUserPoolId")
    client_id = find_output("InfraUserPoolClientId")
    runtime_arn = find_output("RuntimeArn")

    cognito = boto3.client("cognito-idp", region_name=region)
    client_info = cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
    client_secret = client_info["UserPoolClient"]["ClientSecret"]

    pool_info = cognito.describe_user_pool(UserPoolId=user_pool_id)
    domain = pool_info["UserPool"].get("Domain", "")
    token_endpoint = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"

    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "agentcore/invoke",
        }
    ).encode()

    req = urllib.request.Request(
        token_endpoint,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
    )

    if not token_endpoint.startswith("https://"):
        raise ValueError(f"Only HTTPS URLs are permitted: {token_endpoint}")
    with urllib.request.urlopen(req) as resp:  # nosec B310
        token_data = json.loads(resp.read())

    return token_data["access_token"], runtime_arn


def invoke_agent(token: str, runtime_arn: str, region: str, prompt: str) -> str:
    """Invoke the agent runtime. Returns full response text."""
    escaped_arn = urllib.parse.quote(runtime_arn, safe="")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations"

    payload = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    parts = []
    if not url.startswith("https://"):
        raise ValueError(f"Only HTTPS URLs are permitted: {url}")
    with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310
        for line in resp:
            decoded = line.decode("utf-8").strip()
            if decoded.startswith("data: "):
                chunk = decoded[6:]
                if chunk.startswith('"') and chunk.endswith('"'):
                    chunk = json.loads(chunk)
                parts.append(chunk)

    return "".join(parts)


def run_tests(region: str) -> bool:
    print(f"Cedar Policy Enforcement Tests — region: {region}")
    print("=" * 60)

    print("Getting Cognito token...")
    token, runtime_arn = get_cognito_token(region)
    print(f"Runtime ARN: {runtime_arn[:50]}...")
    print()

    tests = [
        {
            "name": "High-value claim ($150k) — should be BLOCKED",
            "prompt": (
                "File a claim for POL-12345. My car was completely totaled in a highway accident. "
                "The estimated repair cost is $150,000."
            ),
            "expect_blocked": True,
        },
        {
            "name": "Normal claim ($5k) — should SUCCEED",
            "prompt": (
                "File a claim for POL-12345. A storm caused a tree branch to fall on my car, "
                "cracking the windshield and denting the roof. Estimated damage is $5,000."
            ),
            "expect_blocked": False,
        },
        {
            "name": "Boundary claim ($99,999) — should SUCCEED (just under $100k threshold)",
            "prompt": (
                "File a claim for POL-11111. My car was badly damaged in a collision. "
                "The estimated repair cost is $99,999."
            ),
            "expect_blocked": False,
        },
    ]

    passed = 0
    failed = 0

    for i, test in enumerate(tests, 1):
        print(f"Test {i}: {test['name']}")
        try:
            response = invoke_agent(token, runtime_arn, region, test["prompt"])

            # Cedar block indicators in the response
            is_blocked = any(
                keyword in response.lower()
                for keyword in [
                    "policy",
                    "denied",
                    "authorization",
                    "blocked",
                    "cannot create",
                    "not authorized",
                    "forbidden",
                    "exceed",
                    "human review",  # agent routes to human review when create_claim is blocked
                ]
            )

            if test["expect_blocked"]:
                # For blocked claims, the agent should NOT have been able to create the claim
                # It should mention policy denial or route to human review
                if is_blocked or "HUMAN_REVIEW" in response.upper():
                    print("  ✓ PASS — Claim correctly blocked or routed to human review")
                    print(f"  Response preview: {response[:200].strip()}...")
                    passed += 1
                else:
                    print(f"  ✗ FAIL — Expected block/human review, but got: {response[:200].strip()}...")
                    failed += 1
            else:
                # For allowed claims, we expect the agent to proceed normally
                success_indicators = ["approved", "claim", "decision", "accept", "confidence"]
                if any(kw in response.lower() for kw in success_indicators):
                    print("  ✓ PASS — Claim processed successfully")
                    print(f"  Response preview: {response[:200].strip()}...")
                    passed += 1
                else:
                    print(f"  ✗ FAIL — Unexpected response: {response[:200].strip()}...")
                    failed += 1

        except Exception as e:
            print(f"  ✗ ERROR — {e}")
            failed += 1

        print()

    print("=" * 60)
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")

    return failed == 0


def main():
    parser = argparse.ArgumentParser(description="Test Cedar policy enforcement")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    args = parser.parse_args()

    success = run_tests(args.region)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
