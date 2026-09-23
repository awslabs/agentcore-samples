"""Attach an MCP HTTP passthrough target fronting the GitHub MCP server.

Creates an http.passthrough target (protocolType MCP) to
https://api.githubcopilot.com/mcp/ on the shared no-auth gateway. Outbound auth
uses JWT_PASSTHROUGH: the gateway forwards the caller's inbound Authorization
header (the client's own GitHub token, sent as `Authorization: Bearer <token>`)
to the GitHub MCP server unchanged.

Requires GATEWAY_ID in environment or .env (run deploy_gateway.py first).

Usage:
    uv run python scripts/github-mcp-passthrough/deploy.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

TARGET_NAME = "github"
GITHUB_MCP_ENDPOINT = "https://api.githubcopilot.com/mcp/"


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
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    print(f"--- Creating MCP HTTP passthrough target '{TARGET_NAME}' ---")
    # JWT_PASSTHROUGH outbound: the gateway forwards the caller's inbound
    # Authorization header (the client's GitHub token) to the GitHub MCP server
    # unchanged. MCP protocolType gets a default schema, so no schema is provided.
    target = control.create_gateway_target(
        name=TARGET_NAME,
        gatewayIdentifier=gateway_id,
        targetConfiguration={
            "http": {
                "passthrough": {
                    "endpoint": GITHUB_MCP_ENDPOINT,
                    "protocolType": "MCP",
                }
            }
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "JWT_PASSTHROUGH"}
        ],
        # MCP streamable-http issues an Mcp-Session-Id on initialize that the
        # client echoes on later calls, and replies with SSE (text/event-stream).
        # Allowlist both so MCP clients (e.g. the MCP Inspector) can complete the
        # handshake and parse the stream through the gateway.
        metadataConfiguration={
            "allowedRequestHeaders": [
                "Mcp-Session-Id",
                "Content-Type",
                "Accept",
            ],
            "allowedResponseHeaders": [
                "Mcp-Session-Id",
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
