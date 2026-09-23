"""Demo: MCP response streaming through AgentCore Gateway.

Subcommands:
    backward-compat    Accept: application/json — single buffered JSON response
    progress           Server-emitted progress notifications (streaming_demo)
    failing            Mid-stream tool exception (failing_demo)
    logging            Server-emitted log events (logging_demo)
    keepalive          Long-running keep-alive via 30s progress (keepalive_demo)

Requires GATEWAY_URL and COGNITO_STACK_NAME environment variables,
or a .env file at the gatewaylabproject root.

Usage:
    uv run python scripts/streaming/demo.py backward-compat
    uv run python scripts/streaming/demo.py progress
    uv run python scripts/streaming/demo.py failing
    uv run python scripts/streaming/demo.py logging
    uv run python scripts/streaming/demo.py keepalive
"""

import argparse
import json
import os
import sys
import time

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_mcp_client import GatewayMCPClient

TARGET = "streaming-mcp-server-target"


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
    import uuid

    return GatewayMCPClient(
        gateway_url,
        token_fn,
        protocol_version="2025-11-25",
        session_id=str(uuid.uuid4()),
    )


def cmd_backward_compat(gateway_url, token_fn):
    """Accept: application/json — single buffered response, no intermediate events."""
    mcp = make_client(gateway_url, token_fn)

    print("=== A) getOrder (no intermediate frames) ===")
    response = mcp.call_tool_json_only(f"{TARGET}___getOrder", {}, request_id=2)
    print(f"HTTP {response['http_status']}  Content-Type: {response['content_type']}")
    print(f"Body: {response['body']}")

    print("\n=== B) streaming_demo (buffered — progress events dropped) ===")
    buf = mcp.call_tool_json_only(
        f"{TARGET}___streaming_demo", {"steps": 5}, request_id=20
    )
    print(f"HTTP {buf['http_status']}  Content-Type: {buf['content_type']}")
    print(f"Body: {buf['body']}")
    print(
        "\nNote: the body contains only the final tool result. The 5 "
        "notifications/progress frames the server emitted were discarded "
        "because the client did not request text/event-stream."
    )


def cmd_progress(gateway_url, token_fn):
    """Server-emitted progress notifications via SSE."""
    mcp = make_client(gateway_url, token_fn)
    print("--- streaming_demo SSE frames ---")
    for msg in mcp.stream_tool_call(
        f"{TARGET}___streaming_demo",
        {"steps": 5},
        progress_token="demo-progress",
        request_id=3,
    ):
        print(json.dumps(msg))


def cmd_failing(gateway_url, token_fn):
    """Mid-stream tool exception — progress frames then isError=true."""
    mcp = make_client(gateway_url, token_fn)
    print("--- failing_demo SSE frames (3 progress, then error) ---")
    for msg in mcp.stream_tool_call(
        f"{TARGET}___failing_demo",
        {"steps": 3},
        progress_token="failing-demo",
        request_id=4,
    ):
        print(json.dumps(msg))


def cmd_logging(gateway_url, token_fn):
    """Server-emitted log events — one per severity level."""
    mcp = make_client(gateway_url, token_fn)
    print("--- logging_demo SSE frames (4 log events + result) ---")
    for msg in mcp.stream_tool_call(
        f"{TARGET}___logging_demo",
        {},
        request_id=5,
    ):
        print(json.dumps(msg))


def cmd_keepalive(gateway_url, token_fn):
    """Long-running keep-alive — 60s with 30s progress heartbeats."""
    mcp = make_client(gateway_url, token_fn)
    started = time.time()
    n_progress = 0
    saw_result = False

    for msg in mcp.stream_tool_call(
        f"{TARGET}___keepalive_demo",
        {"duration_seconds": 60, "interval_seconds": 30, "emit_progress": True},
        progress_token="keepalive-60s",
        request_id=6,
    ):
        if msg.get("method") == "notifications/progress":
            n_progress += 1
            print(f"  progress #{n_progress} at {round(time.time() - started, 1)}s")
        elif msg.get("id") == 6:
            saw_result = True
            print(
                f"  result at {round(time.time() - started, 1)}s: {msg.get('result')}"
            )

    print(
        f"\nelapsed={round(time.time() - started, 1)}s  "
        f"progress_count={n_progress}  result_seen={saw_result}"
    )


def main():
    load_env()

    parser = argparse.ArgumentParser(description="MCP streaming demos")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "backward-compat", help="Accept: application/json buffered response"
    )
    subparsers.add_parser("progress", help="Server-emitted progress notifications")
    subparsers.add_parser("failing", help="Mid-stream tool exception")
    subparsers.add_parser("logging", help="Server-emitted log events")
    subparsers.add_parser("keepalive", help="Long-running keep-alive via 30s progress")

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
        "backward-compat": cmd_backward_compat,
        "progress": cmd_progress,
        "failing": cmd_failing,
        "logging": cmd_logging,
        "keepalive": cmd_keepalive,
    }
    commands[args.command](gateway_url, token_fn)


if __name__ == "__main__":
    main()
