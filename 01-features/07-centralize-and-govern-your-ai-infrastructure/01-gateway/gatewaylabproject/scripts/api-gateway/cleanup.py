"""Clean up resources created by deploy_targets.py.

Deletes the two gateway targets, API Key credential provider, and the
IAM policies added to the gateway role. Does NOT delete the gateway
itself (created via AgentCore CLI).

Usage:
    uv run python scripts/api-gateway/cleanup.py
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


def main():
    load_env()
    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    gateway_id = os.environ.get("GATEWAY_ID", "")
    if not gateway_id:
        print("ERROR: GATEWAY_ID not set. Run deploy_targets.py first.")
        sys.exit(1)

    target_names = ["api-gateway-target-pets", "api-gateway-target-orders"]

    if gateway_id:
        print("--- Deleting gateway targets ---")
        try:
            targets = admin.client.list_gateway_targets(
                gatewayIdentifier=gateway_id, maxResults=20
            )
            for item in targets.get("items", []):
                if item.get("name") in target_names:
                    print(f"  Deleting target: {item['name']}")
                    admin.client.delete_gateway_target(
                        gatewayIdentifier=gateway_id, targetId=item["targetId"]
                    )
        except Exception as e:
            print(f"  Error: {e}")

        print("\n--- Removing IAM policies from gateway role ---")
        try:
            gw = admin.client.get_gateway(gatewayIdentifier=gateway_id)
            role_name = gw["roleArn"].split("/")[-1]
            for policy_name in ["ApiGatewayInvokePolicy", "ApiKeyCredentialPolicy"]:
                try:
                    admin.iam.delete_role_policy(
                        RoleName=role_name, PolicyName=policy_name
                    )
                    print(f"  Removed {policy_name} from {role_name}")
                except Exception:
                    pass
        except Exception as e:
            print(f"  Error: {e}")

    print("\n--- Deleting API Key credential provider ---")
    try:
        admin.client.delete_api_key_credential_provider(name="apigw-orders-api-key")
        print("  Deleted: apigw-orders-api-key")
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    main()
