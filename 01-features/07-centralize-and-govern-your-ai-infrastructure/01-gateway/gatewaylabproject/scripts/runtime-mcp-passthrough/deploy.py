"""Attach an MCP HTTP passthrough target fronting an MCP server on AgentCore Runtime.

Creates an http.passthrough target (protocolType MCP) whose endpoint is the
runtime's invocation URL (capture it with `agentcore status --json` -> the
agent's invocationUrl). Outbound auth uses JWT_PASSTHROUGH: the gateway forwards
the caller's inbound Authorization header (an Entra ID bearer) to the runtime,
whose CUSTOM_JWT inbound auth validates it.

The runtime also requires the X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header,
so the target allowlists it (plus Content-Type and Accept) via
metadataConfiguration.allowedRequestHeaders.

Requires GATEWAY_ID in environment or .env (run deploy_gateway.py first).

Usage:
    uv run python scripts/runtime-mcp-passthrough/deploy.py --endpoint "$RUNTIME_URL"
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

TARGET_NAME = "elicitation-runtime"


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


def save_env(updates):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars.update(updates)
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Create an MCP http.passthrough target for a runtime-hosted MCP server"
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="The runtime invocation URL (from agentcore status --json invocationUrl)",
    )
    args = parser.parse_args()

    load_env()
    gateway_id = get_required_env("GATEWAY_ID")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    print(f"--- Creating MCP HTTP passthrough target '{TARGET_NAME}' ---")
    # JWT_PASSTHROUGH outbound: the gateway forwards the caller's inbound
    # Authorization header (an Entra ID bearer) to the runtime unchanged. The
    # runtime also needs the session-id header, so it is allowlisted alongside
    # Content-Type and Accept. MCP protocolType gets a default schema.
    target = control.create_gateway_target(
        name=TARGET_NAME,
        gatewayIdentifier=gateway_id,
        targetConfiguration={
            "http": {
                "passthrough": {
                    "endpoint": args.endpoint,
                    "protocolType": "MCP",
                }
            }
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "JWT_PASSTHROUGH"}
        ],
        metadataConfiguration={
            "allowedRequestHeaders": [
                "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id",
                # MCP streamable-http issues a session id on initialize that the
                # client must echo on later calls; allow it inbound and outbound.
                "Mcp-Session-Id",
                "Content-Type",
                "Accept",
            ],
            "allowedResponseHeaders": [
                "Mcp-Session-Id",
                # MCP responses are SSE (text/event-stream). Forward the
                # response Content-Type so MCP clients (e.g. a2a/MCP inspectors)
                # parse the stream instead of failing to JSON-decode it.
                "Content-Type",
            ],
        },
    )
    target_id = target["targetId"]
    print(f"  Target ID: {target_id}")

    print("\n  Waiting for target to become READY...")
    while True:
        time.sleep(10)
        t = control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        status = t["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "CREATE_FAILED"]:
            break

    save_env({"TARGET_ID": target_id, "TARGET_NAME": TARGET_NAME})
    print("\n  Saved TARGET_ID and TARGET_NAME to .env")


if __name__ == "__main__":
    main()
