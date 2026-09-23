"""
Clean up all resources created by setup.sh and deploy.py.

Deletes:
- AgentCore Runtime endpoint and runtime
- AgentCore IAM execution role
- CloudFormation stack (VPC, EFS, security group, NAT, etc.)

Note: deleting the CloudFormation stack deletes the EFS file system and every
Codex thread persisted on it.

Usage:
    python cleanup.py
"""

import json
import os
import sys
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Cleanup is best-effort: a resource that is already gone, or a delete that the
# service rejects, must not stop the remaining steps — otherwise a single failure
# leaves the expensive resources (NAT Gateway, EIP, EFS) billing. ClientError
# covers service-side rejections; BotoCoreError covers waiter timeouts and
# connection problems.
AWS_ERRORS = (BotoCoreError, ClientError)


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
    stack_name = env_cfg.get("AGENTCORE_STACK_NAME", "agentcore-codex-demo")

    session = boto3.Session(region_name=region)
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
    except AWS_ERRORS as e:
        print(f"  Warning: {e}")

    # 2. Delete AgentCore runtime
    try:
        print(f"  Deleting runtime: {runtime_id}")
        control.delete_agent_runtime(agentRuntimeId=runtime_id)
        print("  Waiting for runtime deletion...")
        time.sleep(30)
    except AWS_ERRORS as e:
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
    except AWS_ERRORS as e:
        print(f"  Warning: {e}")

    # 4. Delete the ECR repository (created by setup.sh, holds the arm64 image)
    ecr_repo = env_cfg.get("AGENTCORE_ECR_REPO", "agentcore-codex")
    try:
        session.client("ecr").delete_repository(repositoryName=ecr_repo, force=True)
        print(f"  Deleted ECR repository: {ecr_repo}")
    except AWS_ERRORS as e:
        print(f"  Warning: {e}")

    # 5. Delete CloudFormation stack (VPC, EFS, SG, NAT, etc.)
    stack_deleted = False
    try:
        print(f"  Deleting CloudFormation stack: {stack_name}")
        cfn.delete_stack(StackName=stack_name)
        # Deleting the NAT Gateway alone routinely takes several minutes, and
        # AgentCore releases its VPC network interfaces asynchronously after the
        # runtime is gone, which blocks the subnets until it does. Allow 30
        # minutes rather than the 10 a 40x15s waiter would give.
        print("  Waiting for stack deletion (NAT Gateway and ENI release; up to ~30 min)...")
        waiter = cfn.get_waiter("stack_delete_complete")
        waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 30, "MaxAttempts": 60})
        print(f"  Stack deleted: {stack_name}")
        stack_deleted = True
    except AWS_ERRORS as e:
        print(f"  Warning: {e}")

    # 6. Remove local config files, but only if the stack is really gone.
    # Otherwise these files are the only record of what to retry, and a
    # half-deleted stack keeps billing for the NAT Gateway, EIP and EFS.
    if stack_deleted:
        for f in ["runtime_config.json", "envvars.config"]:
            path = os.path.join(os.path.dirname(__file__), f)
            if os.path.exists(path):
                os.remove(path)
        print(f"\nCleanup complete for {agent_name}")
    else:
        print(f"\nStack '{stack_name}' was NOT deleted. Local config files kept so you can retry.")
        print("  Any leftover NAT Gateway, EIP or EFS file system continues to bill hourly.")
        print("\n  The usual cause is DELETE_FAILED on the private subnets: AgentCore")
        print("  releases the network interfaces it attached to them only after the")
        print("  runtime is gone, and until then the subnets have dependencies. The")
        print("  interfaces are service-owned, so they cannot be detached by hand.")
        print("  Confirm they are gone, then retry the delete:")
        print(f"    aws ec2 describe-network-interfaces --region {region} \\")
        print(f"        --filters Name=subnet-id,Values={env_cfg.get('AGENTCORE_SUBNET_1', '<subnet-1>')} \\")
        print("        --query 'NetworkInterfaces[].NetworkInterfaceId'")
        print(f"    aws cloudformation delete-stack --stack-name {stack_name} --region {region}")
        sys.exit(1)


if __name__ == "__main__":
    main()
