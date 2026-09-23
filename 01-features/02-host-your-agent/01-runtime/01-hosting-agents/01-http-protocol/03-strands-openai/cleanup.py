"""
Clean up all resources created by deploy.py.

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

    agent_name = config["agent_name"]
    runtime_id = config["runtime_id"]
    region = config["region"]
    account_id = Session(region_name=region).client("sts").get_caller_identity()["Account"]

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    print(f"Cleaning up: {agent_name}\n")

    # 1. Delete endpoints
    try:
        endpoints = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
        for ep in endpoints.get("runtimeEndpoints", []):
            name = ep["name"]
            if name == "DEFAULT":
                continue
            print(f"  Deleting endpoint: {name}")
            control.delete_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=name)
        if endpoints.get("runtimeEndpoints"):
            print("  Waiting for endpoint deletion...")
            time.sleep(30)
    except Exception as e:
        print(f"  Warning: {e}")

    # 2. Delete runtime
    try:
        print(f"  Deleting runtime: {runtime_id}")
        control.delete_agent_runtime(agentRuntimeId=runtime_id)
        print("  Waiting for runtime deletion...")
        time.sleep(30)
    except Exception as e:
        print(f"  Warning: {e}")

    # 3. Delete S3 code artifact
    try:
        bucket = f"agentcore-code-{account_id}-{region}"
        s3.delete_object(Bucket=bucket, Key=f"{agent_name}/code.zip")
        print("  Deleted S3 code artifact")
    except Exception as e:
        print(f"  Warning: {e}")

    # 4. Delete IAM role
    role_name = f"agentcore-{agent_name}-role"
    try:
        for p in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=p)
        iam.delete_role(RoleName=role_name)
        print(f"  Deleted IAM role: {role_name}")
    except Exception as e:
        print(f"  Warning: {e}")

    if os.path.exists("runtime_config.json"):
        os.remove("runtime_config.json")

    print("\n✓ Cleanup complete")


if __name__ == "__main__":
    main()
