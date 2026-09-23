"""Clean up everything created by deploy.py and deploy_gateway.py.

Deletes the three inference targets, the OpenAI/Anthropic API key credential
providers, the gateway, the gateway IAM role (with its policies), and finally
removes the local .env file. Tolerant of already-deleted resources.

Usage:
    uv run python scripts/unified-multi-provider/cleanup.py
"""

import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

GATEWAY_NAME = "unified-multi-provider-gateway"
API_KEY_PROVIDERS = ["unified-openai-key", "unified-anthropic-key"]


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

    if gateway_id:
        print("--- Deleting gateway (targets + gateway) ---")
        try:
            admin.delete_gateway(gateway_id)
        except Exception as e:  # noqa: BLE001 - cleanup tolerates already-deleted
            print(f"  Error: {e}")

    print("--- Deleting API key credential provider(s) ---")
    for cred_name in API_KEY_PROVIDERS:
        try:
            admin.identity_client.delete_api_key_credential_provider(name=cred_name)
            print(f"  Deleted: {cred_name}")
        except Exception as e:  # noqa: BLE001
            print(f"  Skipped {cred_name}: {e}")

    print("--- Deleting gateway IAM role ---")
    try:
        admin.delete_gateway_role(GATEWAY_NAME)
    except Exception as e:  # noqa: BLE001
        print(f"  Error: {e}")

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        os.remove(env_path)
        print(f"--- Removed {env_path} ---")

    print("\nDone.")


if __name__ == "__main__":
    main()
