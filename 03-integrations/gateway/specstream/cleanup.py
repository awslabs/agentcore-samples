"""Delete AWS resources created by deploy.py. Reads .deploy_state.json."""

from __future__ import annotations

import json
import os
import sys
import time

from config import AWS_REGION, STATE_FILE


def cleanup() -> int:
    """Tear down gateway target, gateway, credential provider, and IAM role."""
    if not os.path.exists(STATE_FILE):
        print(f"No deployment state found at {STATE_FILE}. Nothing to clean up.")
        return 1

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    with open(STATE_FILE, encoding="utf-8") as f:
        state = json.load(f)

    region = state.get("region", AWS_REGION)
    gateway_id = state["gateway_id"]
    role_name = state.get("role_name")
    cred_name = state.get("credential_provider_name")

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam", region_name=region)

    try:
        targets = control.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        for target in targets:
            control.delete_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=target["targetId"],
            )
            print(f"  [OK] Deleted target: {target['targetId']}")
        if targets:
            time.sleep(5)
    except (ClientError, BotoCoreError) as exc:
        print(f"  [WARN] Error deleting gateway targets: {exc}")

    try:
        control.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  [OK] Deleted gateway: {gateway_id}")
    except (ClientError, BotoCoreError) as exc:
        print(f"  [WARN] Error deleting gateway: {exc}")

    if cred_name:
        try:
            control.delete_api_key_credential_provider(name=cred_name)
            print(f"  [OK] Deleted credential provider: {cred_name}")
        except (ClientError, BotoCoreError) as exc:
            print(f"  [WARN] Error deleting credential provider: {exc}")

    if role_name:
        try:
            for policy_name in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            iam.delete_role(RoleName=role_name)
            print(f"  [OK] Deleted IAM role: {role_name}")
        except (ClientError, BotoCoreError) as exc:
            print(f"  [WARN] Error deleting IAM role: {exc}")

    os.remove(STATE_FILE)
    print("Cleanup complete.")
    return 0


if __name__ == "__main__":
    sys.exit(cleanup())
