#!/usr/bin/env python3
"""Remove observability deliveries created by enable_observability.py.

Cleans up TRACES + APPLICATION_LOGS delivery chains (deliveries → sources →
destinations) and vendedlog log groups for:
  - Gateway
  - Gateway WorkloadIdentity
  - Memory
  - Runtime WorkloadIdentity

Idempotent — safe to run if resources are already gone. Discovers everything by
the "*-bedrock-agentcore-*" / kind-prefixed delivery source naming convention,
so it works even after the CloudFormation stack is deleted.

Usage:
    python3 scripts/disable_observability.py --region us-west-2
"""

import argparse

import boto3
from botocore.exceptions import ClientError

DEFAULT_STACK_NAME = "AgentCore-ClaimsAgent-dev"

# Delivery source name prefixes created by enable_observability.py
_KIND_PREFIXES = ("gateway-", "gateway-identity-", "memory-", "runtime-identity-")


def get_args():
    parser = argparse.ArgumentParser(description="Remove AgentCore observability deliveries")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    parser.add_argument("--stack-name", default=DEFAULT_STACK_NAME, help="(unused; kept for symmetry)")
    return parser.parse_args()


def _is_ours(name: str) -> bool:
    return name.startswith(_KIND_PREFIXES) and (name.endswith("-traces") or name.endswith("-logs"))


def main():
    args = get_args()
    region = args.region
    logs = boto3.client("logs", region_name=region)

    print("  Discovering observability deliveries...")

    # 1. Delete deliveries + sources for anything we created
    source_names = []
    try:
        paginator = logs.get_paginator("describe_delivery_sources")
        for page in paginator.paginate():
            for src in page.get("deliverySources", []):
                if _is_ours(src.get("name", "")):
                    source_names.append(src["name"])
    except ClientError as e:
        print(f"  ⚠️  describe_delivery_sources: {e}")

    if not source_names:
        print("  No observability deliveries found — nothing to clean up.")
        return

    # Map deliveries by source name so we can delete the link first
    deliveries_by_source = {}
    try:
        paginator = logs.get_paginator("describe_deliveries")
        for page in paginator.paginate():
            for d in page.get("deliveries", []):
                deliveries_by_source.setdefault(d.get("deliverySourceName"), []).append(d["id"])
    except ClientError:
        pass

    for source_name in source_names:
        # Delete delivery links
        for delivery_id in deliveries_by_source.get(source_name, []):
            try:
                logs.delete_delivery(id=delivery_id)
            except ClientError:
                pass
        # Delete the source
        try:
            logs.delete_delivery_source(name=source_name)
        except ClientError:
            pass
        # Delete the matching destination (same name + "-dest")
        try:
            logs.delete_delivery_destination(name=f"{source_name}-dest")
        except ClientError:
            pass

    # 2. Delete vendedlog log groups we created
    try:
        paginator = logs.get_paginator("describe_log_groups")
        prefix = "/aws/vendedlogs/bedrock-agentcore/"
        for page in paginator.paginate(logGroupNamePrefix=prefix):
            for lg in page.get("logGroups", []):
                name = lg.get("logGroupName", "")
                if any(f"/{k.rstrip('-')}/" in name for k in _KIND_PREFIXES):
                    try:
                        logs.delete_log_group(logGroupName=name)
                    except ClientError:
                        pass
    except ClientError:
        pass

    print(f"  ✓ Removed {len(source_names)} delivery sources + destinations + log groups")


if __name__ == "__main__":
    main()
