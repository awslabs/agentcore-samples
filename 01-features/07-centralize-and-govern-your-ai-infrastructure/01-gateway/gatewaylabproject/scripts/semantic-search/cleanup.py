"""Clean up resources created by deploy.py for the Semantic Search tutorial.

Deletes:
  1. Gateway targets and gateway
  2. Gateway IAM role

Lambda functions are managed by CloudFormation — delete the stack separately.
Cognito is the shared pool from 00-optional-setup — not deleted here.

Usage:
    uv run python scripts/semantic-search/cleanup.py
"""

import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

GATEWAY_NAME = "gateway-search-tutorial"


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
        print("ERROR: GATEWAY_ID not set. Run deploy.py first.")
        sys.exit(1)

    # 1. Delete all targets, wait for deletion, then delete gateway
    print("--- Deleting Gateway Targets ---")
    try:
        targets = admin.client.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=50
        )
        for t in targets.get("items", []):
            tid = t["targetId"]
            print(f"  Deleting target: {tid}")
            try:
                admin.client.delete_gateway_target(
                    gatewayIdentifier=gateway_id, targetId=tid
                )
            except ClientError as e:
                if "ResourceNotFoundException" not in str(e):
                    print(f"    Error: {e}")

        # Wait for all targets to finish deleting
        print("  Waiting for targets to finish deleting...")
        for _ in range(12):
            time.sleep(10)
            remaining = admin.client.list_gateway_targets(
                gatewayIdentifier=gateway_id, maxResults=50
            )
            if not remaining.get("items"):
                break
        else:
            print("  WARNING: Targets still present after 2 minutes")
    except ClientError as e:
        if "ResourceNotFoundException" not in str(e):
            print(f"  Error listing targets: {e}")

    print("--- Deleting Gateway ---")
    try:
        admin.client.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  Gateway deleted: {gateway_id}")
    except ClientError as e:
        if "ResourceNotFoundException" in str(e):
            print(f"  Gateway not found (already deleted): {gateway_id}")
        else:
            print(f"  Error deleting gateway: {e}")

    # 2. Delete gateway IAM role
    print("\n--- Deleting Gateway IAM Role ---")
    admin.delete_gateway_role(GATEWAY_NAME)

    # 3. Remove local .env
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        os.remove(env_path)
        print(f"\n  Removed {env_path}")

    print("\nCleanup complete.")
    print("\n  Now delete the Lambda CloudFormation stack:")
    print("    aws cloudformation delete-stack --stack-name $LAMBDA_STACK_NAME")
    print("    aws s3 rb s3://$CFN_BUCKET --force")


if __name__ == "__main__":
    main()
