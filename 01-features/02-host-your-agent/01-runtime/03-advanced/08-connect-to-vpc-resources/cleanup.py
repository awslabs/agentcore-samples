"""
Clean up all resources created by deploy.py.

Deletes:
- AgentCore Runtime endpoint and runtime
- S3 code artifact
- IAM execution role
- CDK infrastructure (VPC, Fargate, security groups)
- Generated config files

Usage:
    python cleanup.py
"""

import json
import os
import subprocess
import sys
import time

import boto3
from boto3.session import Session


def load_config() -> dict:
    try:
        with open("runtime_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found.")
        sys.exit(1)


def main():
    config = load_config()
    agent_name = config["agent_name"]
    runtime_id = config["runtime_id"]
    region = config["region"]

    session = Session(region_name=region)
    account_id = session.client("sts").get_caller_identity()["Account"]
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    print(f"Cleaning up resources for: {agent_name}\n")

    # 1. Delete endpoints
    try:
        endpoints = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
        for ep in endpoints.get("runtimeEndpoints", []):
            name = ep["name"]
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
    bucket_name = f"agentcore-code-{account_id}-{region}"
    s3_key = f"{agent_name}/code.zip"
    try:
        s3.delete_object(Bucket=bucket_name, Key=s3_key)
        print(f"  Deleted s3://{bucket_name}/{s3_key}")
    except Exception as e:
        print(f"  Warning: {e}")

    # 4. Delete IAM role
    role_name = f"agentcore-{agent_name}-role"
    try:
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
        print(f"  Deleted IAM role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  IAM role not found: {role_name}")
    except Exception as e:
        print(f"  Warning: {e}")

    # 5. Destroy CDK infrastructure
    print("\n  Destroying CDK infrastructure...")
    try:
        subprocess.run(
            ["npx", "cdk", "destroy", "--force"],
            check=True,
        )
        print("  ✓ CDK infrastructure destroyed")
    except subprocess.CalledProcessError as e:
        print(f"  Warning: CDK destroy failed: {e}")
    except FileNotFoundError:
        print("  Warning: npx not found, skipping CDK destroy")

    # 6. Remove generated config files
    for config_file in ["runtime_config.json", "cdk-outputs.json"]:
        if os.path.exists(config_file):
            os.remove(config_file)
            print(f"  Removed {config_file}")

    print(f"\n✓ Cleanup complete for {agent_name}")


if __name__ == "__main__":
    main()
