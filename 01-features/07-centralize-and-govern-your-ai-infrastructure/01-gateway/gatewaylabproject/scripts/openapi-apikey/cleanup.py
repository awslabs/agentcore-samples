"""Clean up resources created by deploy_target.py.

Deletes the API key credential provider, S3 bucket with spec, and
IAM policies added to the gateway role. Does NOT delete the gateway
itself (created via AgentCore CLI).

Usage:
    uv run python scripts/openapi-apikey/cleanup.py
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
    s3_bucket = os.environ.get("S3_BUCKET", "")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    if gateway_id:
        print("--- Deleting gateway target ---")
        try:
            targets = admin.client.list_gateway_targets(
                gatewayIdentifier=gateway_id, maxResults=20
            )
            for item in targets.get("items", []):
                if item.get("name") == "openapi-apikey-nasa-target":
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
            for policy_name in ["OpenApiSpecS3ReadPolicy", "ApiKeyCredentialPolicy"]:
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
        admin.client.delete_api_key_credential_provider(name="nasa-openapi-api-key")
        print("  Deleted: nasa-openapi-api-key")
    except Exception as e:
        print(f"  Error: {e}")

    if s3_bucket:
        print(f"\n--- Deleting S3 bucket: {s3_bucket} ---")
        try:
            s3 = boto3.client("s3", region_name=region)
            objects = s3.list_objects_v2(Bucket=s3_bucket)
            for obj in objects.get("Contents", []):
                s3.delete_object(Bucket=s3_bucket, Key=obj["Key"])
            s3.delete_bucket(Bucket=s3_bucket)
            print(f"  Deleted bucket: {s3_bucket}")
        except Exception as e:
            print(f"  Error: {e}")


if __name__ == "__main__":
    main()
