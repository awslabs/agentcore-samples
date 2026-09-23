"""Demo: MCP session management through AgentCore Gateway.

Subcommands:
    initialize         Initialize a session and print the Mcp-Session-Id
    continuity         Show session_counter incrementing across calls
    isolation          Two sessions with independent state
    error-contract     Missing / fake Mcp-Session-Id probes
    performance        Cold-start vs warm invocation timing

Requires GATEWAY_URL and COGNITO_STACK_NAME environment variables,
or a .env file at the gatewaylabproject root.

Usage:
    uv run python scripts/sessions/demo.py initialize
    uv run python scripts/sessions/demo.py continuity
    uv run python scripts/sessions/demo.py isolation
    uv run python scripts/sessions/demo.py error-contract
    uv run python scripts/sessions/demo.py performance
"""

import argparse
import os
import sys
import uuid

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_mcp_client import GatewayMCPClient

TARGET = "session-mcp-server-target"


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


def make_client(gateway_url, token_fn):
    return GatewayMCPClient(
        gateway_url,
        token_fn,
        protocol_version="2025-11-25",
        session_id=str(uuid.uuid4()),
    )


def call_session_counter(client, label):
    msg = client.call_tool(f"{TARGET}___session_counter", {})
    print(f"  [{label}] result={msg.get('result', {}).get('structuredContent')}")
    return msg


def cmd_initialize(gateway_url, token_fn):
    """Initialize a session and print the Mcp-Session-Id."""
    mcp = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")
    init = mcp.initialize(client_info={"name": "session-demo", "version": "0.1"})
    print(f"init HTTP {init['http_status']}")
    print(f"Session id: {mcp.session_id}")


def cmd_continuity(gateway_url, token_fn):
    """Show session_counter incrementing across calls within one session."""
    mcp = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")
    mcp.initialize(client_info={"name": "session-demo", "version": "0.1"})
    print(f"Session id: {mcp.session_id}\n")

    print("Four calls on the same session:")
    call_session_counter(mcp, "1")
    call_session_counter(mcp, "2")
    call_session_counter(mcp, "3")
    call_session_counter(mcp, "4")


def cmd_isolation(gateway_url, token_fn):
    """Two sessions with independent state, then resume the first."""
    # Session A
    mcp_a = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")
    mcp_a.initialize(client_info={"name": "session-demo", "version": "0.1"})
    print(f"Session A id: {mcp_a.session_id}")

    print("\nThree calls on session A:")
    call_session_counter(mcp_a, "A.1")
    call_session_counter(mcp_a, "A.2")
    call_session_counter(mcp_a, "A.3")

    # Session B
    mcp_b = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")
    mcp_b.initialize(client_info={"name": "session-demo", "version": "0.1"})
    print(f"\nSession B id: {mcp_b.session_id}  (distinct from A: {mcp_a.session_id})")

    print("\nTwo calls on session B:")
    call_session_counter(mcp_b, "B.1")
    call_session_counter(mcp_b, "B.2")

    print("\nBack to session A — count continues from where it left off:")
    call_session_counter(mcp_a, "A.4")


def cmd_error_contract(gateway_url, token_fn):
    """Probe missing and fake Mcp-Session-Id behavior."""
    # Probe 1: no session id
    mcp_no_sid = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")
    r = mcp_no_sid.rpc_raw("tools/list")
    print(f"NO Mcp-Session-Id           -> HTTP {r.status_code}  body={r.text[:120]!r}")

    # Probe 2: fake session id
    fake_sid = str(uuid.uuid4())
    mcp_fake = GatewayMCPClient(
        gateway_url, token_fn, protocol_version="2025-11-25", session_id=fake_sid
    )
    r = mcp_fake.rpc_raw("tools/list")
    print(
        f"FAKE Mcp-Session-Id={fake_sid}  -> HTTP {r.status_code}  body={r.text[:120]!r}"
    )


def cmd_performance(gateway_url, token_fn):
    """Cold-start vs warm invocation timing."""
    import time

    mcp = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")
    mcp.initialize(client_info={"name": "session-demo", "version": "0.1"})
    print(f"Session id: {mcp.session_id}\n")

    # First call — cold start
    t0 = time.time()
    call_session_counter(mcp, "1 (cold start)")
    cold = time.time() - t0
    print(f"  elapsed: {cold:.2f}s\n")

    # Second call — warm
    t0 = time.time()
    call_session_counter(mcp, "2 (warm)")
    warm = time.time() - t0
    print(f"  elapsed: {warm:.2f}s\n")

    print(f"Cold: {cold:.2f}s  Warm: {warm:.2f}s  Speedup: {cold / warm:.1f}x")


def main():
    load_env()

    parser = argparse.ArgumentParser(description="MCP session management demos")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("initialize", help="Initialize a session and print the id")
    subparsers.add_parser(
        "continuity", help="Show session_counter incrementing across calls"
    )
    subparsers.add_parser("isolation", help="Two sessions with independent state")
    subparsers.add_parser("error-contract", help="Missing / fake Mcp-Session-Id probes")
    subparsers.add_parser("performance", help="Cold-start vs warm invocation timing")

    args = parser.parse_args()

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

    commands = {
        "initialize": cmd_initialize,
        "continuity": cmd_continuity,
        "isolation": cmd_isolation,
        "error-contract": cmd_error_contract,
        "performance": cmd_performance,
    }
    commands[args.command](gateway_url, token_fn)


if __name__ == "__main__":
    main()
