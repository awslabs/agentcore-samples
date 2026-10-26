"""Clean up everything created by deploy.py and deploy_gateway.py.

Deletes the bedrock-mantle and openai inference targets, the OpenAI API key
credential provider, the gateway, the gateway IAM role, and finally removes the
local .env file. Tolerant of already-deleted resources.

Usage:
    uv run python scripts/inference-iam-inbound/cleanup.py
"""

import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from env_utils import load_env
from gateway_admin import GatewayBoto3Client

GATEWAY_NAME = "inference-iam-inbound-gateway"
API_KEY_PROVIDERS = ["iam-inbound-openai-key"]


def main():
    load_env(os.path.join(os.path.dirname(__file__), ".env"))

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
