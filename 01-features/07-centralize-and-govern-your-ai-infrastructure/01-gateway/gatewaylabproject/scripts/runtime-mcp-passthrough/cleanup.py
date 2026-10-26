"""Clean up resources created by the runtime-mcp-passthrough scripts.

The no-auth gateway (context7-gateway) is shared with the Context7 and GitHub MCP
labs, so this script removes only this lab's own passthrough target. It deletes
the shared gateway and its IAM role only when no targets remain on the gateway.
Tolerant of already-deleted resources. Does not delete the AgentCore Runtime
agent (remove that with `agentcore remove agent`).

Usage:
    uv run python scripts/runtime-mcp-passthrough/cleanup.py
"""

import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

GATEWAY_NAME = "context7-gateway"
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


def main():
    load_env()

    gateway_id = os.environ.get("GATEWAY_ID", "")
    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    remaining_targets = None
    if gateway_id:
        print(f"--- Deleting this lab's target '{TARGET_NAME}' ---")
        try:
            targets = control.list_gateway_targets(
                gatewayIdentifier=gateway_id, maxResults=50
            ).get("items", [])
            for t in targets:
                if t.get("name") == TARGET_NAME:
                    control.delete_gateway_target(
                        gatewayIdentifier=gateway_id, targetId=t["targetId"]
                    )
                    print(f"  Deleted target: {t['targetId']}")
            # Recount after deletion to decide whether the gateway is now empty.
            remaining_targets = control.list_gateway_targets(
                gatewayIdentifier=gateway_id, maxResults=50
            ).get("items", [])
        except Exception as e:  # noqa: BLE001 - cleanup tolerates already-deleted
            print(f"  Error: {e}")

    # Only tear down the shared gateway when no targets are left (the other MCP
    # passthrough labs have also been cleaned up).
    if gateway_id and remaining_targets == []:
        print(f"--- Gateway '{GATEWAY_NAME}' has no targets left, deleting it ---")
        try:
            control.delete_gateway(gatewayIdentifier=gateway_id)
            print(f"  Deleted gateway: {gateway_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  Error: {e}")
        print("--- Deleting gateway IAM role ---")
        try:
            admin.delete_gateway_role(GATEWAY_NAME)
        except Exception as e:  # noqa: BLE001
            print(f"  Error: {e}")
    elif remaining_targets:
        names = ", ".join(t.get("name", "?") for t in remaining_targets)
        print(
            f"--- Leaving shared gateway '{GATEWAY_NAME}' in place "
            f"(targets still attached: {names}) ---"
        )

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        os.remove(env_path)
        print(f"--- Removed {env_path} ---")

    print("\nDone.")


if __name__ == "__main__":
    main()
