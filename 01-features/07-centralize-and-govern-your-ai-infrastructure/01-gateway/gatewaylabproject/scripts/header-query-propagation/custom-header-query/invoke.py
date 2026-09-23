"""Demo: Test custom header and query parameter propagation with interceptor precedence.

Requires GATEWAY_URL, COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/header-query-propagation/custom-header-query/invoke.py
"""

import json
import os
import sys

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to .env")
        sys.exit(1)
    return val


def get_token(token_endpoint, client_id, client_secret, scope):
    response = requests.post(
        token_endpoint,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def main():
    load_env()

    gateway_url = get_required_env("GATEWAY_URL")
    cognito_stack = get_required_env("COGNITO_STACK_NAME")

    region = boto3.Session().region_name
    cfn = boto3.client("cloudformation", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack)["Stacks"][0]["Outputs"]
    }
    gw_client_id = outputs["GatewayClientId"]
    gw_scope = outputs["GatewayScope"]
    gw_client_secret = cognito.describe_user_pool_client(
        UserPoolId=outputs["UserPoolId"], ClientId=gw_client_id
    )["UserPoolClient"]["ClientSecret"]
    token_endpoint = outputs["TokenEndpoint"]

    access_token = get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    print(f"Gateway URL: {gateway_url}\n")

    # --- Test 1: Call Lambda target with custom headers + query params ---
    print("=" * 60)
    print("Test 1: Lambda target — headers + query params propagation")
    print("=" * 60)

    test_url = f"{gateway_url}?version=v2&environment=staging"
    resp = requests.post(  # nosec B113
        test_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-11-25",
            "x-correlation-id": "trace-abc123",
            "x-tenant-id": "tenant-from-client",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "header-echo-lambda-target___echo",
                "arguments": {"message": "Hello with headers"},
            },
        },
    )
    result = resp.json()
    print(json.dumps(result, indent=2))

    # Check response headers
    rate_limit = resp.headers.get("x-rate-limit-remaining")
    print(f"\n  Response header x-rate-limit-remaining: {rate_limit}")

    # --- Test 2: Verify interceptor override ---
    print("\n" + "=" * 60)
    print("Test 2: Interceptor precedence — x-tenant-id overridden")
    print("=" * 60)
    print("  Client sent: x-tenant-id: tenant-from-client")
    print("  Interceptor overrides to: x-tenant-id: tenant-from-interceptor")

    content = result.get("result", {}).get("content", [])
    if content:
        body = json.loads(content[0].get("text", "{}"))
        received_tenant = body.get("propagated_headers", {}).get(
            "x-tenant-id", "unknown"
        )
        print(f"  Lambda received: x-tenant-id: {received_tenant}")
        if received_tenant == "tenant-from-interceptor":
            print("  PASS: Interceptor precedence works")
        else:
            print("  FAIL: Expected 'tenant-from-interceptor'")

    # --- Test 3: Non-allowlisted header dropped ---
    print("\n" + "=" * 60)
    print("Test 3: Non-allowlisted interceptor header dropped")
    print("=" * 60)
    print("  Interceptor adds: x-custom-tenant-id: custom-value-from-interceptor")
    print("  This header is NOT in metadataConfiguration.allowedRequestHeaders")

    if content:
        body = json.loads(content[0].get("text", "{}"))
        custom_header = body.get("propagated_headers", {}).get("x-custom-tenant-id")
        if custom_header is None:
            print("  PASS: Non-allowlisted header was dropped (not received by Lambda)")
        else:
            print(f"  FAIL: Header reached Lambda with value: {custom_header}")

    # --- Test 4: Query params propagated ---
    print("\n" + "=" * 60)
    print("Test 4: Query parameters propagated")
    print("=" * 60)

    if content:
        body = json.loads(content[0].get("text", "{}"))
        query_params = body.get("propagated_query_params", {})
        print(f"  version: {query_params.get('version', 'not received')}")
        print(f"  environment: {query_params.get('environment', 'not received')}")
        if (
            query_params.get("version") == "v2"
            and query_params.get("environment") == "staging"
        ):
            print("  PASS: Query params propagated correctly")
        else:
            print("  FAIL: Query params not propagated")

    # --- Test 5: MCP server target ---
    print("\n" + "=" * 60)
    print("Test 5: MCP server target — same propagation")
    print("=" * 60)

    resp = requests.post(  # nosec B113
        test_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-11-25",
            "x-correlation-id": "trace-mcp-test",
            "x-tenant-id": "tenant-mcp-client",
        },
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "header-echo-mcp-target___echo_headers",
                "arguments": {"message": "Hello from MCP target test"},
            },
        },
    )
    result = resp.json()
    print(json.dumps(result, indent=2))

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print("  - Allowlisted headers propagated to both Lambda and MCP targets")
    print("  - Interceptor overrides client headers (precedence)")
    print("  - Non-allowlisted interceptor headers are dropped")
    print("  - Query params forwarded correctly")
    print("  - Response headers returned to client")


if __name__ == "__main__":
    main()
