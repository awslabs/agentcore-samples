"""Delete a gateway target by name.

Shared script used by all VPC egress labs for cleanup.

Usage:
    python scripts/delete_target.py --name my-mcp-target
"""

import argparse
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it first.")
        sys.exit(1)
    return val


def main():
    parser = argparse.ArgumentParser(description="Delete a gateway target by name")
    parser.add_argument("--name", required=True, help="Target name to delete")
    args = parser.parse_args()

    gateway_id = get_required_env("GATEWAY_ID")
    region = boto3.Session().region_name
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"--- Deleting target '{args.name}' from gateway {gateway_id} ---")

    try:
        targets = control.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=50
        )
        target_id = next(
            (t["targetId"] for t in targets.get("items", []) if t["name"] == args.name),
            None,
        )
        if not target_id:
            print(f"  Target not found: {args.name}")
            return

        control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        print(f"  Deleted target: {args.name} ({target_id})")

        print("  Waiting for deletion to complete...")
        time.sleep(10)
        print("  Done.")

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  Target already deleted: {args.name}")
        else:
            print(f"  Error: {e}")


if __name__ == "__main__":
    main()
