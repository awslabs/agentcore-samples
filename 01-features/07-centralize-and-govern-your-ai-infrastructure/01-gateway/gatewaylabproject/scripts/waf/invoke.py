"""Demo: show an allowed request reaching the target, and a WAF-blocked request.

With the Exa MCP target behind the gateway and an AWS WAF web ACL associated:
- A normal tools/list + tools/call reaches the Exa MCP server (allowed).
- Rapid repeated calls trip the rate-based rule; the gateway then returns a
  JSON-RPC error -32002 "Authorization error - Request forbidden" (blocked).

Rate-based rules use a ~5-minute window and sampling, so the blocked path is
best-effort in a short demo: the allowed call always runs; the blocked call is
attempted and reported but does not fail the script if the limit has not tripped.

Requires GATEWAY_URL and COGNITO_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/waf/invoke.py
"""

import os
import sys

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_mcp_client import GatewayMCPClient

BURST = 150  # exceeds the deploy.py rate limit (100 / 5-min window)


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
        print(f"ERROR: {key} not set. Export it or add to the script .env")
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

    def token_fn():
        return get_token(token_endpoint, gw_client_id, gw_client_secret, gw_scope)

    mcp = GatewayMCPClient(gateway_url, token_fn)
    print(f"Gateway URL: {gateway_url}\n")

    # --- Allowed request: list tools surfaced from the Exa MCP target ---
    print("=" * 60)
    print("Allowed request: tools/list (reaches the Exa MCP target)")
    print("=" * 60)
    tools = mcp.list_all_tools()
    for t in tools:
        print(f"  {t['name']}")

    # --- Blocked request: trip the rate-based rule with a burst ---
    print("\n" + "=" * 60)
    print(f"Blocked request: sending a burst of {BURST} calls to trip the rate rule")
    print("=" * 60)
    blocked = False
    for i in range(BURST):
        # rpc_raw returns the un-parsed HTTP response. A WAF block surfaces as
        # HTTP 403 or a JSON-RPC error -32002 "Request forbidden" in the body.
        resp = mcp.rpc_raw("tools/list", {})
        body = resp.text
        if resp.status_code == 403 or "-32002" in body or "forbidden" in body.lower():
            print(f"  Request {i + 1}: BLOCKED by AWS WAF (HTTP {resp.status_code})")
            print(f"    {body[:200]}")
            blocked = True
            break
    if not blocked:
        print(
            "  Rate limit not tripped in this run (WAF rate windows are ~5 min and "
            "sampled). Re-run, or check the WafBlocks CloudWatch metric in the "
            "AWS/Bedrock-AgentCore namespace."
        )


if __name__ == "__main__":
    main()
