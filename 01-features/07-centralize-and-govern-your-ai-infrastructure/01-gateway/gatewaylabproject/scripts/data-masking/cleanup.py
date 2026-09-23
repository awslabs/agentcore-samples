"""Clean up resources created by deploy.py.

Deletes the gateway target. Gateway + IAM role deletion is handled
by cleanup_gateway.py. CloudFormation stack is deleted manually.

Requires GATEWAY_ID in environment or .env (populated by deploy.py).

Usage:
    uv run python scripts/data-masking/cleanup.py
"""

import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


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
    control_client = boto3.client("bedrock-agentcore-control", region_name=region)

    gateway_id = os.environ.get("GATEWAY_ID", "")

    if not gateway_id:
        print("ERROR: GATEWAY_ID not set. Run deploy.py first.")
        sys.exit(1)

    # --- Delete gateway targets ---
    print("--- Deleting gateway targets ---")
    try:
        targets = control_client.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=20
        )
        for item in targets.get("items", []):
            print(f"  Deleting target: {item['name']}")
            control_client.delete_gateway_target(
                gatewayIdentifier=gateway_id, targetId=item["targetId"]
            )
    except Exception as e:
        print(f"  Error listing/deleting targets: {e}")

    print("\nDone. Now run:")
    print(
        "  uv run python scripts/cleanup_gateway.py --name data-masking-gateway --env-file scripts/data-masking/.env"
    )
    print("  aws cloudformation delete-stack --stack-name $LAMBDA_STACK_NAME")
    print("  aws s3 rb s3://$CFN_BUCKET --force")


if __name__ == "__main__":
    main()
