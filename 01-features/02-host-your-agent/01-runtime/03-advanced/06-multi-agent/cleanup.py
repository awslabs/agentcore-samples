"""
Clean up all three agents (orchestrator + specialists).

Usage:
    python cleanup.py
"""

import json
import os
import sys
import time

import boto3
from boto3.session import Session


def main():
    try:
        with open("runtime_config.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found.")
        sys.exit(1)

    region = config["region"]
    account_id = Session(region_name=region).client("sts").get_caller_identity()["Account"]
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    bucket = f"agentcore-code-{account_id}-{region}"

    print("═══ Cleaning up Multi-Agent System ═══\n")

    for agent_name, info in config.get("agents", {}).items():
        runtime_id = info["runtime_id"]
        print(f"── Deleting {agent_name} ──")

        # Delete endpoints
        try:
            for ep in control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id).get("runtimeEndpoints", []):
                control.delete_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=ep["name"])
        except Exception as e:
            print(f"  Warning (endpoints): {e}")

        # Delete runtime
        try:
            control.delete_agent_runtime(agentRuntimeId=runtime_id)
            print("  ✓ Runtime deleted")
        except Exception as e:
            print(f"  Warning (runtime): {e}")

        # Delete S3 code
        try:
            s3.delete_object(Bucket=bucket, Key=f"{agent_name}/code.zip")
        except Exception:
            pass

        # Delete IAM role
        role_name = f"agentcore-{agent_name}-role"
        try:
            for p in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            iam.delete_role(RoleName=role_name)
            print("  ✓ IAM role deleted")
        except Exception:
            pass

    # Wait for deletions to propagate
    print("\n  Waiting for deletions to complete...")
    time.sleep(30)

    if os.path.exists("runtime_config.json"):
        os.remove("runtime_config.json")

    print("\n✓ All agents cleaned up")


if __name__ == "__main__":
    main()
