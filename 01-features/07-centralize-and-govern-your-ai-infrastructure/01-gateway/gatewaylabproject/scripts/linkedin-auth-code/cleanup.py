"""Clean up all resources created by the GitHub auth code flow tutorial.

Deletes gateway targets, gateway, credential provider, and IAM role.

Usage:
    uv run python scripts/linkedin-auth-code/cleanup.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

GATEWAY_NAME = "linkedin-auth-code-gateway"


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
    if not gateway_id:
        print("ERROR: GATEWAY_ID not set. Run deploy_gateway.py first.")
        sys.exit(1)

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    print("--- Deleting gateway targets ---")
    try:
        targets = admin.client.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=20
        )
        for item in targets.get("items", []):
            print(f"  Deleting: {item['name']}")
            admin.client.delete_gateway_target(
                gatewayIdentifier=gateway_id, targetId=item["targetId"]
            )
            time.sleep(5)
    except Exception as e:  # noqa: BLE001
        print(f"  Error: {e}")

    print("\n--- Deleting gateway ---")
    try:
        admin.client.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  Deleted gateway: {gateway_id}")
    except Exception as e:  # noqa: BLE001
        print(f"  Error: {e}")

    print("\n--- Deleting IAM role ---")
    try:
        admin.delete_gateway_role(GATEWAY_NAME)
    except Exception as e:  # noqa: BLE001
        print(f"  Error: {e}")

    print("\n--- Deleting credential provider ---")
    try:
        admin.client.delete_oauth2_credential_provider(name="linkedin-oauth-credential")
        print("  Deleted: linkedin-oauth-credential")
    except Exception as e:  # noqa: BLE001
        print(f"  Error: {e}")


if __name__ == "__main__":
    main()
