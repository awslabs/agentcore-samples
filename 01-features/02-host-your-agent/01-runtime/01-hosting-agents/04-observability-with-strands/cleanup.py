"""
Delete all AWS resources created by deploy.py.

Reads runtime_config.json and removes:
  - AgentCore Runtime endpoints
  - AgentCore Runtime
  - IAM role and inline policies
  - S3 deployment artifacts

Usage:
    python cleanup.py
"""

import json
import sys
import time

import boto3

# ── Load Config ────────────────────────────────────────────────────────────────


def load_config():
    try:
        with open("runtime_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        print("runtime_config.json not found. Nothing to clean up.")
        sys.exit(0)


# ── Cleanup ────────────────────────────────────────────────────────────────────


def cleanup(config):
    region = config["region"]
    runtime_id = config["runtime_id"]
    role_name = config["role_name"]
    s3_bucket = config["s3_bucket"]
    s3_prefix = config["s3_prefix"]

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    # Delete endpoints
    print(f"Deleting endpoints for runtime {runtime_id}...")
    try:
        eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
        for ep in eps.get("runtimeEndpoints", []):
            ep_name = ep["name"]
            control.delete_agent_runtime_endpoint(agentRuntimeId=runtime_id, name=ep_name)
            print(f"  Deleted endpoint: {ep_name}")
        # Wait for deletion
        time.sleep(10)
    except Exception as e:
        print(f"  Warning: {e}")

    # Delete runtime
    print(f"Deleting AgentCore Runtime {runtime_id}...")
    try:
        control.delete_agent_runtime(agentRuntimeId=runtime_id)
        print("  Runtime deletion initiated")
    except Exception as e:
        print(f"  Warning: {e}")

    # Delete IAM role
    print(f"Deleting IAM role {role_name}...")
    try:
        policies = iam.list_role_policies(RoleName=role_name)["PolicyNames"]
        for p in policies:
            iam.delete_role_policy(RoleName=role_name, PolicyName=p)
        iam.delete_role(RoleName=role_name)
        print(f"  Deleted role: {role_name}")
    except Exception as e:
        print(f"  Warning: {e}")

    # Delete S3 artifacts
    print(f"Deleting S3 object s3://{s3_bucket}/{s3_prefix}...")
    try:
        s3.delete_object(Bucket=s3_bucket, Key=s3_prefix)
        print("  Deleted S3 object")
    except Exception as e:
        print(f"  Warning: {e}")

    print("\nCleanup complete.")


def main():
    config = load_config()
    cleanup(config)


if __name__ == "__main__":
    main()
