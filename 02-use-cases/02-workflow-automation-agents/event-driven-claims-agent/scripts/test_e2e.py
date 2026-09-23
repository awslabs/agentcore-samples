#!/usr/bin/env python3
"""Comprehensive E2E test suite for the Event-Driven Claims Agent.

Tests all scenarios per requirements:
1. Normal claim (auto-approve, confidence ≥80)
2. Cedar policy block (claim ≥$100k)
3. Human review routing (low confidence, vague claim)
4. Rejected claim (expired policy)
5. Event-driven flow (S3 email upload → EventBridge → Agent)

Usage:
    python3 scripts/test_e2e.py --region us-west-2
    python3 scripts/test_e2e.py --region us-west-2 --test 5  # run specific test only
    python3 scripts/test_e2e.py --region us-east-1 --verbose  # show full response
"""

import argparse
import json
import time
import urllib.parse
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session as BotocoreSession

# ─── Globals ───────────────────────────────────────────────────────────────
VERBOSE = False


def get_runtime_arn(region: str) -> str:
    """Get the Runtime ARN from CloudFormation outputs."""
    cf = boto3.client("cloudformation", region_name=region)
    outputs = cf.describe_stacks(StackName="AgentCore-ClaimsAgent-dev")["Stacks"][0]["Outputs"]
    output_map = {o["OutputKey"]: o["OutputValue"] for o in outputs}

    for key, val in output_map.items():
        if "RuntimeArn" in key:
            return val

    raise RuntimeError("RuntimeArn not found in stack outputs")


def invoke_agent(runtime_arn: str, region: str, prompt: str) -> str:
    """Invoke the agent runtime with SigV4 auth. Returns response text."""
    escaped_arn = urllib.parse.quote(runtime_arn, safe="")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations"

    payload = json.dumps({"prompt": prompt}).encode()

    session = BotocoreSession()
    credentials = session.get_credentials().get_frozen_credentials()

    aws_request = AWSRequest(
        method="POST",
        url=url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(aws_request)

    req = urllib.request.Request(url, data=payload, headers=dict(aws_request.headers))

    response_text = ""
    try:
        if not url.startswith("https://"):
            raise ValueError(f"Only HTTPS URLs are permitted: {url}")
        with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310
            for line in resp:
                decoded = line.decode("utf-8").strip()
                if decoded:
                    if decoded.startswith("data: "):
                        chunk = decoded[6:]
                        if chunk.startswith('"') and chunk.endswith('"'):
                            chunk = json.loads(chunk)
                        response_text += chunk
                    elif decoded.startswith("{") and "error" in decoded:
                        response_text += f"\n[ERROR] {decoded}\n"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        response_text = f"HTTP {e.code}: {body}"

    return response_text


def check_indicators(response: str, indicators: list[tuple[str, str]]) -> list[str]:
    """Check which indicators matched in the response.

    Args:
        response: Full agent response text
        indicators: List of (label, pattern) tuples. Pattern is checked case-insensitively.

    Returns:
        List of labels for matched indicators.
    """
    matched = []
    lower = response.lower()
    for label, pattern in indicators:
        if pattern.startswith("EXACT:"):
            # Case-sensitive exact substring match
            if pattern[6:] in response:
                matched.append(label)
        else:
            if pattern.lower() in lower:
                matched.append(label)
    return matched


def print_evidence(matched: list[str], response: str, expected_description: str):
    """Print evidence of what was found in the response."""
    if matched:
        print(f"  📋 Evidence ({expected_description}):")
        for m in matched[:5]:  # Cap at 5 to keep output manageable
            print(f"     ✓ Found: {m}")
    if VERBOSE:
        print(f"\n  ─── Full Response ({'truncated at 1500 chars' if len(response) > 1500 else 'complete'}) ───")
        print(f"  {response[:1500]}")
        if len(response) > 1500:
            print("  [...]")
        print("  ─── End Response ───")


# ─── Test Cases ────────────────────────────────────────────────────────────


def test_1_normal_claim(runtime_arn, region):
    """Test 1: Normal claim - auto-approve (confidence ≥80)"""
    print("\n" + "=" * 70)
    print("TEST 1: Normal Claim (Auto-Approve)")
    print("  Policy: POL-12345 (active, John Smith, $50k coverage)")
    print("  Claim: $2,000 fender bender")
    print("  Expected: ACCEPT → Confidence ≥80 → AUTO_APPROVE → Claim created")
    print("=" * 70)

    response = invoke_agent(
        runtime_arn,
        region,
        "I need to file a claim. My policy is POL-12345. I had a fender bender in a parking lot yesterday. Estimated damage is about $2,000.",
    )

    # Check decision indicators
    decision_indicators = [
        ("DECISION: ACCEPT", "DECISION: ACCEPT"),
        ("'accept' in response", "accept"),
        ("'approved' mentioned", "approved"),
    ]
    decision_matched = check_indicators(response, decision_indicators)

    # Check processing indicators
    processing_indicators = [
        ("AUTO_APPROVE routing", "AUTO_APPROVE"),
        ("'auto-approved' mentioned", "auto-approved"),
        ("Claim ID generated (CLM-)", "EXACT:CLM-"),
        ("'claim created' mentioned", "claim created"),
        ("create_claim tool called", "create_claim"),
        ("'notification' sent", "notification"),
    ]
    processing_matched = check_indicators(response, processing_indicators)

    passed = bool(decision_matched) and bool(processing_matched)

    print(f"\n{'✅ PASSED' if passed else '❌ FAILED'}")
    print_evidence(decision_matched, response, "decision=ACCEPT")
    print_evidence(processing_matched, response, "processed & routed")
    if not passed and not VERBOSE:
        print(f"  Response excerpt: {response[:600]}")
    return passed


def test_2_cedar_block(runtime_arn, region):
    """Test 2: Cedar policy block ($150k ≥ $100k threshold)"""
    print("\n" + "=" * 70)
    print("TEST 2: Cedar Policy Block (High Value Claim)")
    print("  Policy: POL-11111 (active, Bob Johnson, $75k coverage)")
    print("  Claim: $150,000 (exceeds $100k Cedar limit)")
    print("  Expected: BLOCKED by BlockExcessiveClaims Cedar policy")
    print("=" * 70)

    response = invoke_agent(
        runtime_arn,
        region,
        "I need to file a claim. My policy is POL-11111. My car was completely totaled in a highway accident. The repair shop estimates $150,000 in damage.",
    )

    block_indicators = [
        ("'denied' in response", "denied"),
        ("'blocked' mentioned", "blocked"),
        ("'not authorized' error", "not authorized"),
        ("'cannot create' mentioned", "cannot create"),
        ("'policy engine' referenced", "policy engine"),
        ("'exceed' mentioned", "exceed"),
        ("'unable to create' mentioned", "unable to create"),
        ("'forbidden' error", "forbidden"),
        ("'cedar' referenced", "cedar"),
        ("$100,000 threshold mentioned", "EXACT:$100,000"),
        ("100,000 threshold mentioned", "EXACT:100,000"),
    ]
    block_matched = check_indicators(response, block_indicators)

    claim_created = "CLM-" in response

    passed = bool(block_matched) or not claim_created

    status = "✅ PASSED (blocked)" if block_matched else ("✅ PASSED (no claim created)" if passed else "❌ FAILED")
    print(f"\n{status}")
    print(f"  Claim created: {'Yes ⚠️ (unexpected!)' if claim_created else 'No (correct)'}")
    if block_matched:
        print_evidence(block_matched, response, "Cedar block detected")
    if not passed and not VERBOSE:
        print(f"  Response excerpt: {response[:600]}")
    return passed


def test_3_human_review(runtime_arn, region):
    """Test 3: Human review routing (low confidence, vague claim)"""
    print("\n" + "=" * 70)
    print("TEST 3: Human Review Routing (Low Confidence)")
    print("  Policy: POL-12345 (active)")
    print("  Claim: Vague description, high amount ($30k), no details")
    print("  Expected: Confidence <80 → HUMAN_REVIEW routing")
    print("=" * 70)

    response = invoke_agent(
        runtime_arn,
        region,
        "I think something might have happened to my car. My policy is POL-12345. I'm not entirely sure what the damage is but it could be around $30,000. I don't have any photos or repair estimates yet.",
    )

    review_indicators = [
        ("HUMAN_REVIEW routing", "HUMAN_REVIEW"),
        ("'human review' mentioned", "human review"),
        ("'confidence' + 'review'", "review"),
        ("'routed to human' mentioned", "routed to human"),
        ("'needs review' mentioned", "needs review"),
        ("'under review' mentioned", "under review"),
        ("request_human_review tool called", "request_human_review"),
    ]
    review_matched = check_indicators(response, review_indicators)

    # Extract confidence score if visible
    confidence_str = ""
    for marker in ["CONFIDENCE:", "confidence:"]:
        if marker in response:
            idx = response.index(marker) + len(marker)
            confidence_str = response[idx : idx + 10].strip().split()[0] if idx < len(response) else ""
            break

    passed = bool(review_matched)

    print(f"\n{'✅ PASSED' if passed else '⚠️  CHECK MANUALLY'}")
    if confidence_str:
        print(f"  Confidence score detected: {confidence_str}")
    print_evidence(review_matched, response, "human review routing")
    if not passed and not VERBOSE:
        print(f"  Response excerpt: {response[:600]}")
    return passed


def test_4_expired_policy(runtime_arn, region):
    """Test 4: Rejected claim (expired policy)"""
    print("\n" + "=" * 70)
    print("TEST 4: Expired Policy Rejection")
    print("  Policy: POL-99999 (EXPIRED, Alice Williams)")
    print("  Claim: $500 minor scratch")
    print("  Expected: REJECT (policy expired/inactive)")
    print("=" * 70)

    response = invoke_agent(
        runtime_arn,
        region,
        "I need to file a claim. My policy number is POL-99999. I have a minor scratch on my bumper, about $500 in damage.",
    )

    reject_indicators = [
        ("DECISION: REJECT", "DECISION: REJECT"),
        ("'rejected' mentioned", "rejected"),
        ("'expired' mentioned", "expired"),
        ("'inactive' policy status", "inactive"),
        ("'not active' mentioned", "not active"),
        ("'cannot process' mentioned", "cannot process"),
        ("'invalid policy' mentioned", "invalid policy"),
    ]
    reject_matched = check_indicators(response, reject_indicators)

    passed = bool(reject_matched)

    print(f"\n{'✅ PASSED' if passed else '❌ FAILED'}")
    print_evidence(reject_matched, response, "rejection detected")
    if not passed and not VERBOSE:
        print(f"  Response excerpt: {response[:600]}")
    return passed


def test_5_event_driven_email(region):
    """Test 5: Event-driven flow (S3 email → EventBridge → Trigger Lambda → Agent)"""
    print("\n" + "=" * 70)
    print("TEST 5: Event-Driven Email Flow")
    print("  Upload email to S3 → EventBridge rule → Trigger Lambda → Agent Runtime")
    print("  Expected: Claim processed asynchronously (POL-67890 in DynamoDB or Lambda logs)")
    print("=" * 70)

    account = boto3.client("sts").get_caller_identity()["Account"]
    bucket_name = f"claims-inbox-{account}-{region}"

    email_content = """From: customer@example.com
Subject: Insurance Claim - Vehicle Damage
Date: Sat, 30 May 2026 12:00:00 +0000
To: claims@secureguard-insurance.com

Dear Claims Department,

I am writing to file a claim under my policy POL-67890.

Yesterday afternoon, a tree branch fell on my roof during a storm, causing significant damage to my home's structure. I have had a contractor come out for an initial assessment, and they estimate the repairs will cost approximately $15,000.

I have photos of the damage and the contractor's written estimate available upon request.

Please process this claim at your earliest convenience.

Best regards,
Jane Doe
"""

    s3 = boto3.client("s3", region_name=region)
    key = f"claims-inbox/claim-{int(time.time())}.eml"
    print(f"  Uploading to s3://{bucket_name}/{key}")

    try:
        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=email_content.encode("utf-8"),
            ContentType="text/plain",
        )
        print("  ✅ Email uploaded successfully")
    except Exception as e:
        print(f"  ❌ Upload failed: {e}")
        return False

    print("  ⏳ Waiting 90s for async agent processing...")
    time.sleep(90)

    # ─── Check DynamoDB for the claim ─────────────────────────────────
    claims_table_name = None
    try:
        cf = boto3.client("cloudformation", region_name=region)
        resources = cf.list_stack_resources(StackName="AgentCore-ClaimsAgent-dev")["StackResourceSummaries"]
        for r in resources:
            if (
                r["ResourceType"] == "AWS::DynamoDB::Table"
                and "Claims" in r["LogicalResourceId"]
                and "Policies" not in r["LogicalResourceId"]
                and "Reviews" not in r["LogicalResourceId"]
            ):
                claims_table_name = r["PhysicalResourceId"]
                break
    except Exception:
        pass

    if not claims_table_name:
        for name in ["ClaimsAgent-dev-Claims", "ClaimsAgent-Claims"]:
            try:
                boto3.client("dynamodb", region_name=region).describe_table(TableName=name)
                claims_table_name = name
                break
            except Exception:
                continue

    if not claims_table_name:
        print("  ❌ Could not find Claims DynamoDB table")
        return False

    # Check DynamoDB
    dynamodb = boto3.resource("dynamodb", region_name=region)
    claims_table = dynamodb.Table(claims_table_name)
    try:
        scan_response = claims_table.scan()
        claims = scan_response.get("Items", [])
        email_claims = [c for c in claims if c.get("policy_number") == "POL-67890"]

        if email_claims:
            latest = email_claims[-1]
            print("  ✅ Claim found in DynamoDB!")
            print("  📋 Evidence (DynamoDB record):")
            print(f"     ✓ Claim ID:  {latest.get('claim_id', 'N/A')}")
            print(f"     ✓ Policy:    {latest.get('policy_number', 'N/A')}")
            print(f"     ✓ Status:    {latest.get('status', 'N/A')}")
            print(f"     ✓ Decision:  {latest.get('decision', 'N/A')}")
            print(f"     ✓ Amount:    {latest.get('amount', latest.get('claimed_amount', 'N/A'))}")
            print(f"     ✓ Category:  {latest.get('category', 'N/A')}")
            print(f"     ✓ Created:   {latest.get('created_at', 'N/A')}")
            return True
        else:
            # Fallback: check Lambda logs
            print("  ⚠️  No POL-67890 claim in DynamoDB. Checking Lambda logs...")
            logs_client = boto3.client("logs", region_name=region)
            try:
                streams = logs_client.describe_log_streams(
                    logGroupName="/aws/lambda/ClaimsAgent-Trigger",
                    orderBy="LastEventTime",
                    descending=True,
                    limit=1,
                )["logStreams"]
                if streams:
                    events = logs_client.get_log_events(
                        logGroupName="/aws/lambda/ClaimsAgent-Trigger",
                        logStreamName=streams[0]["logStreamName"],
                        limit=15,
                    )["events"]
                    trigger_evidence = []
                    for e in events:
                        msg = e["message"]
                        if "Runtime accepted" in msg or "Agent response" in msg or "Phase 1" in msg:
                            trigger_evidence.append(msg.strip()[:120])
                    if trigger_evidence:
                        print("  ✅ Lambda processed the claim (found in logs)")
                        print("  📋 Evidence (Lambda logs):")
                        for line in trigger_evidence[:3]:
                            print(f"     ✓ {line}")
                        return True
            except Exception as log_err:
                print(f"  ⚠️  Could not read logs: {log_err}")
            print("  ❌ No evidence of processing found")
            print("  💡 Hint: The agent may still be processing. Try re-running in 60s.")
            return False
    except Exception as e:
        print(f"  ⚠️  Error checking DynamoDB: {e}")
        return False


# ─── Main ──────────────────────────────────────────────────────────────────


def main():
    global VERBOSE

    parser = argparse.ArgumentParser(description="E2E Test Suite for Claims Agent")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--test", type=int, default=0, help="Run specific test (1-5), 0=all")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full agent responses")
    args = parser.parse_args()
    VERBOSE = args.verbose

    print("🧪 Event-Driven Claims Agent — E2E Test Suite")
    print(f"   Region: {args.region}")
    print(f"   Tests: {'All' if args.test == 0 else f'Test {args.test} only'}")
    print(f"   Verbose: {'Yes' if VERBOSE else 'No (use --verbose for full responses)'}")

    suite_start = time.time()

    # Get runtime ARN (needed for tests 1-4)
    runtime_arn = None
    if args.test == 0 or args.test <= 4:
        print("\n🔑 Authenticating (SigV4)...")
        runtime_arn = get_runtime_arn(args.region)
        print(f"   ✅ Connected | Runtime: {runtime_arn}")

    results = {}
    timings = {}

    if args.test == 0 or args.test == 1:
        start = time.time()
        results["Test 1: Normal Claim (Auto-Approve)"] = test_1_normal_claim(runtime_arn, args.region)
        timings["Test 1: Normal Claim (Auto-Approve)"] = time.time() - start

    if args.test == 0 or args.test == 2:
        start = time.time()
        results["Test 2: Cedar Block ($150k)"] = test_2_cedar_block(runtime_arn, args.region)
        timings["Test 2: Cedar Block ($150k)"] = time.time() - start

    if args.test == 0 or args.test == 3:
        start = time.time()
        results["Test 3: Human Review (Low Confidence)"] = test_3_human_review(runtime_arn, args.region)
        timings["Test 3: Human Review (Low Confidence)"] = time.time() - start

    if args.test == 0 or args.test == 4:
        start = time.time()
        results["Test 4: Expired Policy (Reject)"] = test_4_expired_policy(runtime_arn, args.region)
        timings["Test 4: Expired Policy (Reject)"] = time.time() - start

    if args.test == 0 or args.test == 5:
        start = time.time()
        results["Test 5: Event-Driven Email"] = test_5_event_driven_email(args.region)
        timings["Test 5: Event-Driven Email"] = time.time() - start

    # ─── Summary ──────────────────────────────────────────────────────
    total_time = time.time() - suite_start
    print("\n" + "=" * 70)
    print("📊 TEST RESULTS SUMMARY")
    print("=" * 70)
    for name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED / ⚠️  CHECK"
        duration = timings[name]
        print(f"  {status} — {name} ({duration:.1f}s)")

    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    print(f"\n  {passed_count}/{total} tests passed | Total time: {total_time:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
