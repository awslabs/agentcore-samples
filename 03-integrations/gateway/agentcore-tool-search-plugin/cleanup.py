"""
Delete all AWS resources created by this sample.

Usage:
    python cleanup.py
"""

import json
import sys
import time

import boto3

from config import (
    AWS_REGION,
    GATEWAY_NAME,
    GATEWAY_ROLE_NAME,
    LAMBDA_FUNCTION_NAME,
    LAMBDA_ROLE_NAME,
    STATE_FILE,
)


def cleanup():
    if not __import__("os").path.exists(STATE_FILE):
        print("[ERROR] No deployment state found. Nothing to clean up.")
        sys.exit(1)

    with open(STATE_FILE) as f:
        state = json.load(f)

    print("=" * 60)
    print("CLEANING UP RESOURCES")
    print("=" * 60)

    iam = boto3.client("iam", region_name=AWS_REGION)
    lambda_client = boto3.client("lambda", region_name=AWS_REGION)
    agentcore_control = boto3.client("bedrock-agentcore-control", region_name=AWS_REGION)
    gateway_id = state["gateway_id"]

    # Deregister tool targets
    try:
        targets = agentcore_control.list_gateway_targets(gatewayIdentifier=gateway_id)
        for target in targets.get("items", []):
            agentcore_control.delete_gateway_target(
                gatewayIdentifier=gateway_id, targetId=target["targetId"]
            )
        print("  [OK] Deregistered tool targets from gateway")
    except Exception as e:
        print(f"  [WARN] Error deregistering targets: {e}")

    # Delete gateway
    time.sleep(10)
    try:
        agentcore_control.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  [OK] Deleted gateway: {GATEWAY_NAME}")
    except Exception as e:
        print(f"  [WARN] Error deleting gateway: {e}")

    # Delete Lambda function
    try:
        lambda_client.delete_function(FunctionName=LAMBDA_FUNCTION_NAME)
        print(f"  [OK] Deleted Lambda: {LAMBDA_FUNCTION_NAME}")
    except Exception as e:
        print(f"  [WARN] Error deleting Lambda: {e}")

    # Delete Lambda IAM role
    try:
        iam.detach_role_policy(
            RoleName=LAMBDA_ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        iam.delete_role(RoleName=LAMBDA_ROLE_NAME)
        print(f"  [OK] Deleted IAM role: {LAMBDA_ROLE_NAME}")
    except Exception as e:
        print(f"  [WARN] Error deleting Lambda role: {e}")

    # Delete Gateway IAM role
    try:
        iam.detach_role_policy(
            RoleName=GATEWAY_ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaRole",
        )
        iam.delete_role(RoleName=GATEWAY_ROLE_NAME)
        print(f"  [OK] Deleted IAM role: {GATEWAY_ROLE_NAME}")
    except Exception as e:
        print(f"  [WARN] Error deleting Gateway role: {e}")

    # Remove state file
    __import__("os").unlink(STATE_FILE)
    print()
    print("  [OK] Cleanup complete. All resources removed.")


if __name__ == "__main__":
    cleanup()
