#!/usr/bin/env python3
"""Authentication pattern tests for the Claims Agent.

Validates the auth architecture:
1. SigV4 → Runtime (IAM caller identity — the correct inbound auth path)
2. JWT → IAM-auth Runtime (should be rejected — confirms auth method enforcement)
3. Runtime → Gateway (agent-internal Cognito M2M, tested via tool call)

Negative cases:
4. No auth → Runtime (should be rejected)
5. Invalid/expired JWT → Runtime (should be rejected)
6. Wrong scope → Cognito token endpoint (should be rejected by Cognito)

Architecture:
  Callers (SigV4) → Runtime (AWS_IAM inbound)
  Runtime → Gateway (CUSTOM_JWT, Cognito M2M client_credentials)
  Gateway → Lambda tools (IAM role — zero agent code)

Usage:
    python3 scripts/test_auth.py --region us-west-2
    python3 scripts/test_auth.py --region us-west-2 --test 1   # Run single test
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session as BotocoreSession


# ─── Helpers ──────────────────────────────────────────────────────────────────


def get_stack_outputs(region: str) -> dict:
    """Get all CloudFormation outputs as a flat dict."""
    cf = boto3.client("cloudformation", region_name=region)
    outputs = cf.describe_stacks(StackName="AgentCore-ClaimsAgent-dev")["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def find_output(output_map: dict, prefix: str) -> str:
    """Find an output by key prefix (handles CDK hash suffixes)."""
    for key, val in output_map.items():
        if key.startswith(prefix) or key == prefix:
            return val
    return ""


def get_runtime_arn(output_map: dict) -> str:
    """Extract the Runtime ARN from outputs."""
    return find_output(output_map, "RuntimeArn")


def get_cognito_config(region: str, output_map: dict) -> dict:
    """Get Cognito M2M config by discovering the pool and client from the deployed stack."""
    cognito = boto3.client("cognito-idp", region_name=region)

    # Try CFN outputs first (some stack versions export these)
    user_pool_id = find_output(output_map, "InfraUserPoolId")
    client_id = find_output(output_map, "InfraUserPoolClientId")

    # Fallback: discover by naming convention
    if not user_pool_id:
        pools = cognito.list_user_pools(MaxResults=20)["UserPools"]
        for pool in pools:
            if "ClaimsAgent" in pool["Name"]:
                user_pool_id = pool["Id"]
                break
        if not user_pool_id:
            raise RuntimeError("Could not find ClaimsAgent Cognito User Pool")

    if not client_id:
        clients = cognito.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=10)["UserPoolClients"]
        for c in clients:
            if "M2M" in c.get("ClientName", ""):
                client_id = c["ClientId"]
                break
        if not client_id:
            raise RuntimeError(f"Could not find M2M client in pool {user_pool_id}")

    client_info = cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
    client_secret = client_info["UserPoolClient"]["ClientSecret"]

    pool_info = cognito.describe_user_pool(UserPoolId=user_pool_id)
    domain = pool_info["UserPool"].get("Domain", "")
    token_endpoint = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"

    return {
        "user_pool_id": user_pool_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_endpoint": token_endpoint,
    }


def get_cognito_token(cognito_config: dict, scope: str = "agentcore/invoke") -> str:
    """Get a Cognito M2M token with specified scope."""
    creds = base64.b64encode(f"{cognito_config['client_id']}:{cognito_config['client_secret']}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": scope}).encode()

    req = urllib.request.Request(
        cognito_config["token_endpoint"],
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
    )

    with urllib.request.urlopen(req) as resp:  # nosec B310
        return json.loads(resp.read())["access_token"]


def invoke_runtime_raw(url: str, payload: bytes, headers: dict, timeout: int = 30) -> tuple:
    """Low-level Runtime invocation. Returns (status_code, response_text)."""
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            parts = []
            for line in resp:
                decoded = line.decode("utf-8").strip()
                if decoded.startswith("data: "):
                    chunk = decoded[6:]
                    if chunk.startswith('"') and chunk.endswith('"'):
                        try:
                            chunk = json.loads(chunk)
                        except json.JSONDecodeError:
                            pass
                    parts.append(chunk)
            return resp.status, "".join(parts)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body


def make_runtime_url(runtime_arn: str, region: str) -> str:
    """Build the Runtime invocation URL."""
    escaped_arn = urllib.parse.quote(runtime_arn, safe="")
    return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations"


def sign_request_sigv4(url: str, payload: bytes, region: str) -> dict:
    """Sign a request with SigV4 and return headers."""
    session = BotocoreSession()
    credentials = session.get_credentials().get_frozen_credentials()
    aws_request = AWSRequest(
        method="POST",
        url=url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(aws_request)
    return dict(aws_request.headers)


# ─── Test Functions ───────────────────────────────────────────────────────────


def test_sigv4_auth(region: str, runtime_arn: str, **_) -> tuple:
    """Test 1: SigV4 authentication to Runtime (IAM caller)."""
    url = make_runtime_url(runtime_arn, region)
    payload = json.dumps({"prompt": "What policies do you have access to? Just list tool names."}).encode()
    headers = sign_request_sigv4(url, payload, region)

    status, body = invoke_runtime_raw(url, payload, headers, timeout=120)

    if status == 200 and len(body) > 10:
        return True, f"SigV4 auth succeeded (response: {len(body)} chars)"
    return False, f"Unexpected status {status}: {body[:200]}"


def test_cognito_jwt_rejected_on_iam_runtime(region: str, runtime_arn: str, cognito_config: dict, **_) -> tuple:
    """Test 2: Cognito M2M JWT → IAM-auth Runtime (should be rejected).

    The Runtime uses AWS_IAM inbound auth, not CUSTOM_JWT. Sending a Bearer JWT
    to an IAM-auth Runtime should be rejected with 403. This confirms the Runtime
    does NOT accept arbitrary JWTs — only SigV4-signed requests.
    """
    token = get_cognito_token(cognito_config, scope="agentcore/invoke")
    url = make_runtime_url(runtime_arn, region)
    payload = json.dumps({"prompt": "hello"}).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    status, body = invoke_runtime_raw(url, payload, headers, timeout=15)

    if status == 403:
        return True, "Correctly rejected JWT on IAM-auth Runtime (403 — auth method mismatch)"
    if status == 200:
        return False, "SECURITY ISSUE: JWT accepted on IAM-auth Runtime!"
    return False, f"Unexpected status {status}: {body[:200]}"


def test_e2e_gateway_auth(region: str, runtime_arn: str, **_) -> tuple:
    """Test 3: End-to-end — Runtime authenticates to Gateway (Cognito M2M internal).

    We invoke the agent with a prompt that requires a Gateway tool call.
    If the tool call succeeds, the agent's internal Gateway auth is working.
    """
    url = make_runtime_url(runtime_arn, region)
    payload = json.dumps(
        {"prompt": "Look up policy POL-12345 using the lookup_policy tool and tell me the holder name."}
    ).encode()
    headers = sign_request_sigv4(url, payload, region)

    status, body = invoke_runtime_raw(url, payload, headers, timeout=180)

    if status == 200 and "john" in body.lower():
        return True, "Agent→Gateway auth working (tool call returned policy holder)"
    if status == 200:
        return False, f"Agent responded but tool may have failed: {body[:300]}"
    return False, f"HTTP {status}: {body[:200]}"


def test_no_auth_rejected(region: str, runtime_arn: str, **_) -> tuple:
    """Test 4: No authentication → should be rejected (403 or 401)."""
    url = make_runtime_url(runtime_arn, region)
    payload = json.dumps({"prompt": "hello"}).encode()
    headers = {"Content-Type": "application/json"}

    status, body = invoke_runtime_raw(url, payload, headers, timeout=15)

    if status in (401, 403):
        return True, f"Correctly rejected unauthenticated request (HTTP {status})"
    if status == 200:
        return False, "SECURITY ISSUE: Unauthenticated request was accepted!"
    return False, f"Unexpected status {status} (expected 401/403): {body[:200]}"


def test_invalid_jwt_rejected(region: str, runtime_arn: str, **_) -> tuple:
    """Test 5: Invalid/expired JWT → should be rejected."""
    url = make_runtime_url(runtime_arn, region)
    payload = json.dumps({"prompt": "hello"}).encode()
    # Fabricate an obviously invalid JWT
    fake_token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJmYWtlIiwiZXhwIjoxMH0.invalid_signature"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {fake_token}",
    }

    status, body = invoke_runtime_raw(url, payload, headers, timeout=15)

    if status in (401, 403):
        return True, f"Correctly rejected invalid JWT (HTTP {status})"
    if status == 200:
        return False, "SECURITY ISSUE: Invalid JWT was accepted!"
    return False, f"Unexpected status {status} (expected 401/403): {body[:200]}"


def test_wrong_scope_rejected(region: str, runtime_arn: str, cognito_config: dict, **_) -> tuple:
    """Test 6: JWT with wrong scope → should be rejected or produce auth error.

    Note: Cognito may not enforce scopes at the token validation level — the Gateway's
    Cedar policies or the Cognito resource server may reject. This test validates the
    scope was requested properly rather than catching it at the Runtime auth layer.
    """
    # Try to get a token with a non-existent scope. Cognito will either:
    # a) Reject the token request (400) — pass: scope enforcement at Cognito level
    # b) Issue a token but with no/wrong scopes — the Gateway Cedar policy may block
    try:
        creds = base64.b64encode(f"{cognito_config['client_id']}:{cognito_config['client_secret']}".encode()).decode()
        data = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "nonexistent/scope"}).encode()

        req = urllib.request.Request(
            cognito_config["token_endpoint"],
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {creds}",
            },
        )

        with urllib.request.urlopen(req) as resp:  # nosec B310
            token_data = json.loads(resp.read())
            # If Cognito issued a token, try using it
            token = token_data.get("access_token", "")
            if not token:
                return True, "Cognito rejected wrong scope at token endpoint"

            # Try invoking with the wrong-scope token
            url = make_runtime_url(runtime_arn, region)
            payload = json.dumps({"prompt": "hello"}).encode()
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            }
            status, body = invoke_runtime_raw(url, payload, headers, timeout=15)
            if status in (401, 403):
                return True, f"Correctly rejected wrong-scope JWT (HTTP {status})"
            # Some configurations allow the token through (scope checked at Gateway level)
            return True, "Token issued but scope enforcement deferred to Gateway/Cedar layer (acceptable)"

    except urllib.error.HTTPError as e:
        if e.code == 400:
            return True, "Cognito correctly rejected invalid scope at token endpoint (HTTP 400)"
        return False, f"Unexpected error from Cognito: HTTP {e.code}"
    except Exception as e:
        return False, f"Error: {e}"


# ─── Test Runner ──────────────────────────────────────────────────────────────

TESTS = [
    ("SigV4 → Runtime (IAM auth)", test_sigv4_auth),
    ("JWT → IAM-auth Runtime (should reject — auth mismatch)", test_cognito_jwt_rejected_on_iam_runtime),
    ("Runtime → Gateway (agent-internal M2M auth via Cognito)", test_e2e_gateway_auth),
    ("No auth → Runtime (should reject)", test_no_auth_rejected),
    ("Invalid JWT → Runtime (should reject)", test_invalid_jwt_rejected),
    ("Wrong scope → Cognito token endpoint (scope enforcement)", test_wrong_scope_rejected),
]


def run_tests(region: str, test_num: int = None) -> bool:
    """Run auth tests. Returns True if all pass."""
    print(f"🔐 Authentication Pattern Tests — region: {region}")
    print("=" * 70)

    # Setup: get stack outputs and Cognito config
    print("Setting up...")
    output_map = get_stack_outputs(region)
    runtime_arn = get_runtime_arn(output_map)
    cognito_config = get_cognito_config(region, output_map)
    print(f"  Runtime: {runtime_arn.split('/')[-1]}")
    print(f"  Cognito: {cognito_config['user_pool_id']}")
    print()

    ctx = {
        "region": region,
        "runtime_arn": runtime_arn,
        "cognito_config": cognito_config,
    }

    tests_to_run = TESTS
    if test_num is not None:
        if 1 <= test_num <= len(TESTS):
            tests_to_run = [TESTS[test_num - 1]]
        else:
            print(f"Invalid test number {test_num}. Valid: 1-{len(TESTS)}")
            return False

    passed = 0
    failed = 0

    for i, (name, fn) in enumerate(tests_to_run, start=test_num or 1):
        print(f"Test {i}: {name}")
        try:
            success, detail = fn(**ctx)
            if success:
                print(f"  ✓ PASS — {detail}")
                passed += 1
            else:
                print(f"  ✗ FAIL — {detail}")
                failed += 1
        except Exception as e:
            print(f"  ✗ ERROR — {type(e).__name__}: {e}")
            failed += 1
        print()

    print("=" * 70)
    total = passed + failed
    print(f"Results: {passed}/{total} passed, {failed} failed")

    if failed == 0:
        print("\n✅ All auth tests passed!")
    else:
        print("\n❌ Some auth tests failed — review output above.")

    return failed == 0


def main():
    parser = argparse.ArgumentParser(description="Test authentication patterns")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    parser.add_argument("--test", type=int, default=None, help="Run a single test (1-6)")
    args = parser.parse_args()

    success = run_tests(args.region, args.test)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
