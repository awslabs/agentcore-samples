"""Delete an AgentCore Gateway, its targets, credential providers, and IAM role.

Used by tutorials where the gateway was created via boto3 (not CLI).

Requires GATEWAY_ID in environment or the specified .env file.

Usage:
    uv run python scripts/cleanup_gateway.py --name streaming-gateway --env-file scripts/streaming/.env
    uv run python scripts/cleanup_gateway.py --name session-gateway --env-file scripts/sessions/.env
    uv run python scripts/cleanup_gateway.py --name elicitation-gateway --env-file scripts/elicitation/.env
"""

import argparse
import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gateway_admin import GatewayBoto3Client


def load_env(env_file):
    if os.path.exists(env_file):
        with open(env_file) as f:
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


def main():
    parser = argparse.ArgumentParser(
        description="Delete an AgentCore Gateway, its targets, credential providers, and IAM role"
    )
    parser.add_argument("--name", required=True, help="Gateway name")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to .env file (default: scripts/<name>/.env)",
    )
    args = parser.parse_args()

    env_file = args.env_file
    if not env_file:
        script_dir = os.path.join(
            os.path.dirname(__file__),
            args.name.replace("-gateway", "").replace("gateway", ""),
        )
        if os.path.isdir(script_dir):
            env_file = os.path.join(script_dir, ".env")
        else:
            env_file = os.path.join(os.path.dirname(__file__), ".env")

    load_env(env_file)

    gateway_id = get_required_env("GATEWAY_ID")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    print(f"--- Listing targets for gateway '{args.name}' ({gateway_id}) ---")
    cred_provider_names = set()
    try:
        targets = control.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=20
        )
        for item in targets.get("items", []):
            target_name = item.get("name", item["targetId"])
            cred_provider_names.add(f"{target_name}-oauth")
    except Exception:
        pass

    print(f"--- Deleting gateway '{args.name}' (targets + gateway) ---")
    admin.delete_gateway(gateway_id)

    for cred_name in cred_provider_names:
        print(f"--- Deleting credential provider '{cred_name}' ---")
        admin.delete_credential_provider(cred_name)

    print(f"--- Deleting gateway IAM role for '{args.name}' ---")
    admin.delete_gateway_role(args.name)

    print("\nDone.")


if __name__ == "__main__":
    main()
