"""Attach a CUSTOM HTTP passthrough target fronting Claude Managed Agents.

Creates an http.passthrough target (protocolType CUSTOM) to
https://api.anthropic.com on the shared gateway. The target has NO credential
provider: HTTP passthrough targets do not support API_KEY outbound, so the
client supplies its own Claude key as `x-api-key` and the gateway forwards it
via header propagation (metadataConfiguration.allowedRequestHeaders).

CUSTOM protocol targets must provide a schema to use policy-engine features such
as guardrails (MCP/A2A get a default schema, CUSTOM does not), so this script
attaches an OpenAPI schema inline.

Requires GATEWAY_ID in environment or .env (run deploy_gateway.py first).

Usage:
    uv run python scripts/managed-agents-custom/deploy.py
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

TARGET_NAME = "claude-managed-agents"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com"
DEFAULT_SCHEMA_FILE = os.path.join(
    os.path.dirname(__file__), "managed-agents-schema.yaml"
)


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
        description="Attach a CUSTOM HTTP passthrough target for Claude Managed Agents"
    )
    parser.add_argument(
        "--schema-file",
        default=DEFAULT_SCHEMA_FILE,
        help="Path to the OpenAPI schema for the CUSTOM target (default: managed-agents-schema.yaml)",
    )
    args = parser.parse_args()

    load_env()

    gateway_id = get_required_env("GATEWAY_ID")

    # CUSTOM protocol targets require a schema for guardrails. Ship it inline so
    # the tutorial needs no S3 bucket.
    with open(args.schema_file) as f:
        schema_payload = f.read()

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    print(f"--- Creating CUSTOM HTTP passthrough target '{TARGET_NAME}' ---")
    # No credentialProviderConfigurations: HTTP passthrough targets do not
    # support API_KEY outbound. The client sends its own Claude key as
    # x-api-key; the gateway forwards it (and the anthropic-* headers) outbound.
    target = control.create_gateway_target(
        name=TARGET_NAME,
        gatewayIdentifier=gateway_id,
        targetConfiguration={
            "http": {
                "passthrough": {
                    "endpoint": ANTHROPIC_ENDPOINT,
                    "protocolType": "CUSTOM",
                    "schema": {"source": {"inlinePayload": schema_payload}},
                }
            }
        },
        metadataConfiguration={
            "allowedRequestHeaders": [
                "x-api-key",
                "anthropic-version",
                "anthropic-beta",
                "content-type",
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
