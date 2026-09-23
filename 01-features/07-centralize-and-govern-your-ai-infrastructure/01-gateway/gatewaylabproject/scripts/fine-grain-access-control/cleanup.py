"""Clean up resources created by deploy.py for the FGAC tutorial.

Deletes gateway targets and credential provider. Gateway + IAM role
deletion is handled by cleanup_gateway.py. CloudFormation stacks
and AgentCore agent are deleted manually.

Requires GATEWAY_ID in environment or .env.

Usage:
    uv run python scripts/fine-grain-access-control/cleanup.py
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
        print("ERROR: GATEWAY_ID not set. Run deploy.py first.")
        sys.exit(1)

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    # --- Delete gateway targets ---
    print("--- Deleting gateway targets ---")
    try:
        targets = control.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=20
        )
        for item in targets.get("items", []):
            print(f"  Deleting target: {item['name']}")
            control.delete_gateway_target(
                gatewayIdentifier=gateway_id, targetId=item["targetId"]
            )
    except Exception as e:
        print(f"  Error listing/deleting targets: {e}")

    # --- Delete credential provider ---
    print("\n--- Deleting credential provider ---")
    admin.delete_credential_provider("fgac-mcp-server-oauth")

    print("\nDone. Now run:")
    print(
        "  uv run python scripts/cleanup_gateway.py --name fgac-gateway --env-file scripts/fine-grain-access-control/.env"
    )
    print("  agentcore remove agent --name fgac_mcp_server -y")
    print("  agentcore deploy --yes")
    print("  aws cloudformation delete-stack --stack-name $FGAC_LAMBDA_STACK_NAME")
    print("  aws cloudformation delete-stack --stack-name $FGAC_COGNITO_STACK_NAME")


if __name__ == "__main__":
    main()
