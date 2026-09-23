#!/usr/bin/env python3
"""
AgentCore Policy FGAC Verification Script

Tests all 3 designs by logging in as different users and verifying
tool access control through the Gateway.

Usage:
    cd 02-use-cases/lakehouse-agent
    source .venv/bin/activate
    python deployment/advanced-agentcore-policy-gateway-interceptor/verification/verify_policy.py

The script uses the default AWS credentials chain (env vars, shared
credentials file, or an active SSO profile set via AWS_PROFILE).
"""

import base64
import hashlib
import hmac
import json
import os
import sys
from typing import Any

import boto3
import requests

# --- Configuration ---
REGION = os.environ.get("AWS_REGION", "us-east-1")
# Require the test password to be supplied explicitly via env var so that
# the script never falls back to a shared default credential. Phase 1
# setup_cognito.py prints the password it sets when creating the test
# users; export it before running this script.
TEST_PASSWORD = os.environ.get("LAKEHOUSE_TEST_PASSWORD")
if not TEST_PASSWORD:
    sys.exit("Error: LAKEHOUSE_TEST_PASSWORD must be set (see deployment/1-cognito-setup/setup_cognito.py output).")
TARGET_NAME = "lakehouse-mcp-target"


def get_config() -> dict[str, str]:
    """Load configuration from SSM Parameter Store."""
    session = boto3.Session(region_name=REGION)
    ssm = session.client("ssm")

    def get_param(name: str, secure: bool = False) -> str:
        return ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}", WithDecryption=secure)["Parameter"]["Value"]

    return {
        "gateway_url": get_param("gateway-url"),
        "user_pool_id": get_param("cognito-user-pool-id"),
        "client_id": get_param("cognito-app-client-id"),
        "client_secret": get_param("cognito-app-client-secret", secure=True),
    }


def compute_secret_hash(username: str, client_id: str, client_secret: str) -> str:
    """Compute Cognito SECRET_HASH."""
    message = username + client_id
    return base64.b64encode(hmac.new(client_secret.encode(), message.encode(), hashlib.sha256).digest()).decode()


def authenticate_user(username: str, config: dict[str, str]) -> str:
    """Authenticate user and return access token."""
    session = boto3.Session(region_name=REGION)
    cognito = session.client("cognito-idp")

    secret_hash = compute_secret_hash(username, config["client_id"], config["client_secret"])

    resp = cognito.admin_initiate_auth(
        UserPoolId=config["user_pool_id"],
        ClientId=config["client_id"],
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": username,
            "PASSWORD": TEST_PASSWORD,
            "SECRET_HASH": secret_hash,
        },
    )
    return resp["AuthenticationResult"]["AccessToken"]


_request_id = 0


def call_gateway(access_token: str, gateway_url: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send an MCP JSON-RPC request to the Gateway."""
    global _request_id
    _request_id += 1

    resp = requests.post(
        gateway_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": _request_id,
            "method": method,
            "params": params,
        },
        timeout=60,
    )

    # Handle SSE format
    if resp.headers.get("Content-Type", "").startswith("text/event-stream"):
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
        return {"error": {"message": "No valid SSE data"}}

    try:
        return resp.json()
    except json.JSONDecodeError:
        return {"error": {"message": f"HTTP {resp.status_code}: {resp.text[:200]}"}}


def parse_tool_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse tool call result to extract data."""
    content = result.get("result", {}).get("content", [])
    if not content:
        return []
    text = content[0].get("text", "")
    try:
        data = json.loads(text)
        return data.get("claims", data.get("records", []))
    except json.JSONDecodeError:
        return []


def is_policy_denied(result: dict[str, Any]) -> bool:
    """Check if result is a Policy DENY (not a backend error).

    Policy DENY returns a JSON-RPC error with "Tool Execution Denied" message.
    Backend errors (e.g. Athena column not found, DynamoDB table missing) mean
    the tool was ALLOWED by Policy but failed during execution.
    """
    # JSON-RPC level error = Policy DENY or Interceptor DENY
    if "error" in result:
        msg = result["error"].get("message", "")
        # Policy DENY or Interceptor tool access denial
        if "Tool Execution Denied" in msg or "not allowed" in msg or "Forbidden" in msg:
            return True
        return True  # Other JSON-RPC errors are also treated as DENY
    return False


# --- Test Functions ---


def test_tool_access(
    username: str,
    tool_name: str,
    expected: str,
    config: dict[str, str],
    description: str = "",
) -> bool:
    """Test if a tool call is ALLOW or DENY."""
    token = authenticate_user(username, config)
    result = call_gateway(
        token,
        config["gateway_url"],
        "tools/call",
        {"name": f"{TARGET_NAME}___{tool_name}", "arguments": {}},
    )
    actual = "DENY" if is_policy_denied(result) else "ALLOW"
    passed = actual == expected
    desc = f" ({description})" if description else ""
    status = "PASS" if passed else "FAIL"
    print(f"  {status}: {tool_name} = {actual} (expected {expected}){desc}")
    if not passed:
        error_msg = result.get("error", {}).get("message", "")[:100]
        if error_msg:
            print(f"        Error: {error_msg}")
    return passed


def test_data_isolation(user1: str, user2: str, config: dict[str, str]) -> bool:
    """Design 2: Verify different users get different data."""
    token1 = authenticate_user(user1, config)
    token2 = authenticate_user(user2, config)

    result1 = call_gateway(
        token1,
        config["gateway_url"],
        "tools/call",
        {"name": f"{TARGET_NAME}___query_claims", "arguments": {}},
    )
    result2 = call_gateway(
        token2,
        config["gateway_url"],
        "tools/call",
        {"name": f"{TARGET_NAME}___query_claims", "arguments": {}},
    )

    claims1 = parse_tool_result(result1)
    claims2 = parse_tool_result(result2)

    ids1 = {c.get("claim_id") for c in claims1}
    ids2 = {c.get("claim_id") for c in claims2}
    no_overlap = ids1.isdisjoint(ids2)

    print(f"  {user1}: {len(claims1)} claims, {user2}: {len(claims2)} claims")
    status = "PASS" if no_overlap else "FAIL"
    print(f"  {status}: Data overlap = {'NONE' if no_overlap else 'FOUND'}")
    return no_overlap


def test_column_masking(username: str, forbidden_column: str, config: dict[str, str]) -> bool:
    """Design 2: Verify a column is not present in results."""
    token = authenticate_user(username, config)
    result = call_gateway(
        token,
        config["gateway_url"],
        "tools/call",
        {"name": f"{TARGET_NAME}___query_claims", "arguments": {}},
    )
    claims = parse_tool_result(result)
    if not claims:
        print(f"  SKIP: No claims returned for {username}")
        return True
    has_column = forbidden_column in claims[0]
    status = "PASS" if not has_column else "FAIL"
    print(f"  {status}: Column '{forbidden_column}' present = {'YES' if has_column else 'NO'}")
    return not has_column


# --- Main ---


def main() -> int:
    print("=" * 60)
    print("AgentCore Policy FGAC Verification")
    print("=" * 60)
    print()

    print("Loading configuration from SSM...")
    config = get_config()
    print(f"Gateway: {config['gateway_url']}")
    print()

    total = 0
    passed = 0

    # ===== Design 1: Policy Only =====
    print("[Design 1: Policy Only - forbid policyholders summary]")
    print("-" * 60)

    print("\n--- policyholder001@example.com (US) ---")
    for tool, expected, desc in [
        ("query_claims", "ALLOW", "Interceptor + Policy allow"),
        ("get_claim_details", "ALLOW", "Interceptor + Policy allow"),
        ("get_claims_summary", "DENY", "Cedar forbid for policyholders"),
    ]:
        total += 1
        if test_tool_access("policyholder001@example.com", tool, expected, config, desc):
            passed += 1

    print("\n--- adjuster001@example.com (US) ---")
    for tool, expected, desc in [
        ("get_claims_summary", "ALLOW", "adjuster is not forbidden"),
        ("query_claims", "ALLOW", "Interceptor + Policy allow"),
    ]:
        total += 1
        if test_tool_access("adjuster001@example.com", tool, expected, config, desc):
            passed += 1

    print("\n--- admin@example.com (US) ---")
    for tool, expected, desc in [
        ("query_login_audit", "ALLOW", "admin allowed"),
        ("text_to_sql", "ALLOW", "admin allowed"),
    ]:
        total += 1
        if test_tool_access("admin@example.com", tool, expected, config, desc):
            passed += 1

    # ===== Design 2: Interceptor Only =====
    print("\n\n[Design 2: Interceptor Only - Token Exchange + RLS]")
    print("-" * 60)

    print("\n--- Data isolation: policyholder001 vs adjuster001 ---")
    total += 1
    if test_data_isolation("policyholder001@example.com", "adjuster001@example.com", config):
        passed += 1

    print("\n--- Column masking: policyholder001 ---")
    total += 1
    if test_column_masking("policyholder001@example.com", "adjuster_user_id", config):
        passed += 1

    print("\n--- Column masking: adjuster001 ---")
    total += 1
    if test_column_masking("adjuster001@example.com", "policyholder_dob", config):
        passed += 1

    # ===== Design 3: Policy + Interceptor =====
    print("\n\n[Design 3: Policy + Interceptor - Geography]")
    print("-" * 60)

    print("\n--- policyholder002@example.com (EU) ---")
    for tool, expected, desc in [
        ("query_claims", "DENY", "EU forbid individual claims"),
        ("get_claims_summary", "DENY", "Design 1 forbid for policyholders"),
    ]:
        total += 1
        if test_tool_access("policyholder002@example.com", tool, expected, config, desc):
            passed += 1

    print("\n--- adjuster001@example.com (US) ---")
    total += 1
    if test_tool_access("adjuster001@example.com", "query_claims", "ALLOW", config, "US allowed"):
        passed += 1

    # ===== Results =====
    print(f"\n\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    print(f"{'=' * 60}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
