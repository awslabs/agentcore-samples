"""Clean up resources created by deploy.py.

Deletes the gateway (and its targets), IAM role, and Lambda CloudFormation stack.

Requires GATEWAY_ID in environment or .env (populated by deploy.py).

Usage:
    uv run python scripts/prevent-sql-injection/cleanup.py
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


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def main():
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")
    gateway_name = os.environ.get("GATEWAY_NAME", "sql-injection-prevention-gateway")
    lambda_stack = os.environ.get(
        "LAMBDA_STACK_NAME", "agentcore-sql-injection-lambdas"
    )

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    cfn = boto3.client("cloudformation", region_name=region)

    # --- Delete gateway (and its targets) ---
    print("--- Deleting gateway and targets ---")
    try:
        admin.delete_gateway(gateway_id)
    except Exception as e:
        print(f"  Error deleting gateway: {e}")

    # --- Delete gateway IAM role ---
    print("\n--- Deleting gateway IAM role ---")
    admin.delete_gateway_role(gateway_name)

    # --- Delete Lambda CloudFormation stack ---
    print(f"\n--- Deleting CloudFormation stack: {lambda_stack} ---")
    try:
        cfn.delete_stack(StackName=lambda_stack)
        print(f"  Stack deletion initiated: {lambda_stack}")
        waiter = cfn.get_waiter("stack_delete_complete")
        waiter.wait(
            StackName=lambda_stack, WaiterConfig={"Delay": 10, "MaxAttempts": 60}
        )
        print(f"  Stack deleted: {lambda_stack}")
    except Exception as e:
        print(f"  Error deleting stack: {e}")

    # --- Remove .env file ---
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        os.remove(env_path)
        print("\n  Removed .env file")

    print("\nDone.")


if __name__ == "__main__":
    main()
