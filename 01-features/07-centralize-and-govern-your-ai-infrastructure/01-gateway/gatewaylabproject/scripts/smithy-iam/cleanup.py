"""Remove the SmithyS3Policy from the gateway IAM role.

Only removes the inline policy added by grant_s3_permissions.py.
Does not delete the gateway or target (those are handled by agentcore CLI).

Requires GATEWAY_ID environment variable (or reads from .env).

Usage:
    uv run python scripts/smithy-iam/cleanup.py
"""

import os
import sys

import boto3


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

    gateway_id = os.environ.get("GATEWAY_ID")
    if not gateway_id:
        print("ERROR: GATEWAY_ID not set. Export it or add to the script .env")
        sys.exit(1)

    region = boto3.Session().region_name
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam")

    try:
        gw = control.get_gateway(gatewayIdentifier=gateway_id)
        role_name = gw["roleArn"].split("/")[-1]
        iam.delete_role_policy(RoleName=role_name, PolicyName="SmithyS3Policy")
        print(f"Removed SmithyS3Policy from {role_name}")
    except Exception as e:
        print(f"Could not remove policy: {e}")


if __name__ == "__main__":
    main()
