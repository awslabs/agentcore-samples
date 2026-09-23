"""Clean up resources created by deploy_target.py.

Deletes the gateway target and Zendesk OAuth credential provider.
Does NOT delete the gateway (created via AgentCore CLI).

Usage:
    uv run python scripts/openapi-oauth/cleanup.py
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

    gateway_id = os.environ.get("GATEWAY_ID", "")
    if not gateway_id:
        print("ERROR: GATEWAY_ID not set. Run deploy_target.py first.")
        sys.exit(1)

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    print("--- Deleting gateway target ---")
    try:
        targets = admin.client.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=20
        )
        for item in targets.get("items", []):
            if item.get("name") == "openapi-zendesk-target":
                print(f"  Deleting: {item['name']}")
                admin.client.delete_gateway_target(
                    gatewayIdentifier=gateway_id, targetId=item["targetId"]
                )
    except Exception as e:
        print(f"  Error: {e}")

    print("\n--- Removing IAM policies from gateway role ---")
    try:
        gw = admin.client.get_gateway(gatewayIdentifier=gateway_id)
        role_name = gw["roleArn"].split("/")[-1]
        admin.iam.delete_role_policy(
            RoleName=role_name, PolicyName="OpenApiSpecS3ReadPolicy"
        )
        print(f"  Removed OpenApiSpecS3ReadPolicy from {role_name}")
    except Exception:
        pass

    print("\n--- Deleting Zendesk credential provider ---")
    try:
        admin.client.delete_oauth2_credential_provider(name="zendesk-oauth-credential")
        print("  Deleted: zendesk-oauth-credential")
    except Exception as e:
        print(f"  Error: {e}")

    print("\n--- Deleting S3 bucket ---")
    try:
        s3 = boto3.client("s3", region_name=region)
        bucket_name = f"agentcore-openapi-specs-{admin.account_id}-{region}"
        objects = s3.list_objects_v2(Bucket=bucket_name)
        for obj in objects.get("Contents", []):
            s3.delete_object(Bucket=bucket_name, Key=obj["Key"])
        s3.delete_bucket(Bucket=bucket_name)
        print(f"  Deleted: {bucket_name}")
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    main()
