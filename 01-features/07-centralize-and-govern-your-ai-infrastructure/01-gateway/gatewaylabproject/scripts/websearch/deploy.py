"""Deploy the Web Search Tool connector as a gateway target.

Creates an MCP target backed by the built-in ``web-search`` connector. The
gateway authenticates to the connector with its own IAM role (GATEWAY_IAM_ROLE),
so no outbound key is stored. The gateway must have been created with the
Web Search permissions (deploy_gateway.py --websearch-targets).

The Web Search Tool connector is available in us-east-1.

Requires GATEWAY_ID in environment or .env.

Usage:
    uv run python scripts/websearch/deploy.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def save_env(updates):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars = {}
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


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def main():
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")
    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    print("--- Creating Web Search connector target ---")
    target = admin.create_mcp_connector_target(
        gateway_id,
        name="web-search-tool",
        connector_id="web-search",
        # The connector requires a configurations list. An empty
        # parameterValues enables the WebSearch tool with its defaults; add a
        # domain filter later with set_domain_filter.py.
        configurations=[{"name": "WebSearch", "parameterValues": {}}],
    )
    target_id = target["targetId"]

    print("\n  Waiting for target to become READY...")
    for _ in range(18):
        time.sleep(10)
        status = admin.client.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )["status"]
        print(f"    Status: {status}")
        if status in ("READY", "FAILED"):
            break

    save_env({"GATEWAY_ID": gateway_id, "TARGET_ID": target_id})
    print("\n  Saved TARGET_ID to .env")
    print("\nNext: uv run python scripts/websearch/invoke.py")
    print("Optional: uv run python scripts/websearch/set_domain_filter.py <domain> ...")


if __name__ == "__main__":
    main()
