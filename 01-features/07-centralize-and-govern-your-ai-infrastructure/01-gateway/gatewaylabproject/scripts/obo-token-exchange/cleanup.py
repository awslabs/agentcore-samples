"""Clean up OBO token exchange resources created by deploy.py.

Deletes the gateway (and its targets), credential provider, and IAM role.

Requires OBO_GATEWAY_ID in environment or .env.

Usage:
    uv run python scripts/obo-token-exchange/cleanup.py
"""

import os
import sys

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


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def main():
    load_env()

    gateway_id = get_required_env("OBO_GATEWAY_ID")
    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    print("--- Deleting gateway and targets ---")
    try:
        admin.delete_gateway(gateway_id)
        print("  Gateway deleted")
    except Exception as e:
        print(f"  Error deleting gateway: {e}")

    print("\n--- Deleting IAM role ---")
    admin.delete_gateway_role("microsoft-obo-gateway")

    print("\n--- Deleting credential provider ---")
    admin.delete_credential_provider("microsoft-obo-provider")

    print("\nDone.")


if __name__ == "__main__":
    main()
