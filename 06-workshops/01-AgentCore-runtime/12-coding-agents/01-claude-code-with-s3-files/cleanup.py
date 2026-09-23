"""
Clean up all resources created by setup.sh and deploy.py.

Deletes everything except the S3 bucket:
- AgentCore Runtime endpoint and runtime
- AgentCore IAM execution role
- CloudFormation stack (VPC, S3 Files, security group, NAT, etc.)

Usage:
    python cleanup.py
"""

import json
import os
import sys
import time

import boto3


def load_config(filename):
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        if filename.endswith(".json"):
            return json.load(f)
        cfg = {}
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                cfg[key] = value.strip('"').strip("'")
        return cfg


def main():
    runtime_cfg = load_config("runtime_config.json")
    env_cfg = load_config("envvars.config")

    if not runtime_cfg:
        print("Error: runtime_config.json not found.")
        sys.exit(1)

    agent_name = runtime_cfg["agent_name"]
    runtime_id = runtime_cfg["runtime_id"]
    region = runtime_cfg["region"]
    stack_name = env_cfg.get("AGENTCORE_STACK_NAME", "agentcore-claude-code")

    session = boto3.Session(region_name=region)
    account_id = session.client("sts").get_caller_identity()["Account"]
    control = session.client("bedrock-agentcore-control", region_name=region)
    iam = session.client("iam")
    cfn = session.client("cloudformation")

    print(f"Cleaning up resources for: {agent_name}\n")

    # 1. Delete AgentCore endpoints
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

    # 2. Delete AgentCore runtime
    try:
        print(f"  Deleting runtime: {runtime_id}")
        control.delete_agent_runtime(agentRuntimeId=runtime_id)
        print("  Waiting for runtime deletion...")
        time.sleep(30)
    except Exception as e:
        print(f"  Warning: {e}")

    # 3. Delete AgentCore IAM execution role
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

    # 4. Delete CloudFormation stack (VPC, S3 Files, SG, NAT, etc.)
    try:
        print(f"  Deleting CloudFormation stack: {stack_name}")
        cfn.delete_stack(StackName=stack_name)
        print("  Waiting for stack deletion (this may take a few minutes)...")
        waiter = cfn.get_waiter("stack_delete_complete")
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 15, "MaxAttempts": 40})
        print(f"  Stack deleted: {stack_name}")
    except Exception as e:
        print(f"  Warning: {e}")

    # 5. Remove local config files
    for f in ["runtime_config.json", "envvars.config"]:
        path = os.path.join(os.path.dirname(__file__), f)
        if os.path.exists(path):
            os.remove(path)

    bucket_name = f"agentcore-{account_id}"
    print(f"\nCleanup complete for {agent_name}")
    print(f"  (bucket s3://{bucket_name} was kept)")


if __name__ == "__main__":
    main()
